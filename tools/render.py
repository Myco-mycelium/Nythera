#!/usr/bin/env python3
"""Render .nstudio files to PNG images.

Usage:
    python3 tools/render.py <file.nstudio> [--backend pil|sdl] [--theme Eclipse|Solar]
                      [--scale 1.0] [--screen SCREEN_ID] [--output DIR]

Examples:
    # Render all screens with PIL backend
    python3 tools/render.py examples/nyrqis-shell/desktop.nstudio

    # Render with SDL2 backend, Solar theme, 2x scale
    python3 tools/render.py examples/nyrqis-shell/desktop.nstudio \\
        --backend sdl --theme Solar --scale 2.0

    # Render a specific screen to a specific output path
    python3 tools/render.py examples/nyrqis-shell/desktop.nstudio \\
        --screen desktop --output /tmp/desktop.png

    # Compare both backends
    python3 tools/render.py examples/nyrqis-shell/desktop.nstudio --compare

When run without arguments, renders all .nstudio fixtures found in
tests/fixtures/nstudio/ and examples/ directories.
"""

import argparse
import os
import sys
import time

# Ensure the backend is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "source", "nyhal-linux-backend"))

from ui.nstudio import load as nstudio_load, NstudioDocument


def render_pil(doc, output_dir, theme, scale, screen_id=None):
    """Render using the PIL compositor."""
    from ui.compositor import Compositor
    comp = Compositor(theme_name=theme, scale=scale)
    screens = [s for s in doc.screens if screen_id is None or s.id == screen_id]
    results = []
    for screen in screens:
        t0 = time.monotonic()
        img = comp.render_screen(doc, screen_id=screen.id)
        elapsed = time.monotonic() - t0
        out = os.path.join(output_dir, f"{screen.id}.png")
        img.save(out)
        size = os.path.getsize(out)
        results.append((screen.id, img.size, size, elapsed))
    return results


def render_sdl(doc, output_dir, theme, scale, screen_id=None):
    """Render using the SDL2 compositor (headless mode)."""
    from ui.compositor_sdl import SDLCompositor, HAS_SDL2
    if not HAS_SDL2:
        print("ERROR: pysdl2 not installed (pip install pysdl2 pysdl2-dll)")
        return []
    comp = SDLCompositor(theme_name=theme, scale=scale, headless=True)
    screens = [s for s in doc.screens if screen_id is None or s.id == screen_id]
    results = []
    for screen in screens:
        t0 = time.monotonic()
        img = comp.render_screen(doc, screen_id=screen.id)
        elapsed = time.monotonic() - t0
        out = os.path.join(output_dir, f"{screen.id}_sdl.png")
        img.save(out)
        size = os.path.getsize(out)
        results.append((screen.id, img.size, size, elapsed))
    return results


def find_fixtures():
    """Find all .nstudio fixture files."""
    fixtures = []
    base = os.path.dirname(__file__)
    for search_dir in [
        os.path.join(base, "..", "tests", "fixtures", "nstudio"),
        os.path.join(base, "..", "source", "nyhal-linux-backend", "tests", "fixtures", "nstudio"),
    ]:
        if os.path.isdir(search_dir):
            for f in sorted(os.listdir(search_dir)):
                if f.endswith(".nstudio"):
                    fixtures.append(os.path.join(search_dir, f))
    # Also check Nyforge examples
    nyforge_dir = os.path.join(base, "..", "..", "Nyforge", "Nyforge", "examples")
    if os.path.isdir(nyforge_dir):
        for root, dirs, files in os.walk(nyforge_dir):
            for f in files:
                if f.endswith(".nstudio"):
                    fixtures.append(os.path.join(root, f))
    return fixtures


def main():
    parser = argparse.ArgumentParser(
        description="Render .nstudio files to PNG images",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("file", nargs="?", help=".nstudio file to render")
    parser.add_argument("--backend", choices=["pil", "sdl", "both"], default="pil",
                        help="Rendering backend (default: pil)")
    parser.add_argument("--theme", choices=["Eclipse", "Solar"], default="Eclipse",
                        help="Theme to use (default: Eclipse)")
    parser.add_argument("--scale", type=float, default=1.0,
                        help="Rendering scale factor (default: 1.0)")
    parser.add_argument("--screen", default=None,
                        help="Render only this screen ID")
    parser.add_argument("--output", "-o", default=None,
                        help="Output directory or file (default: ./render_output/)")
    parser.add_argument("--compare", action="store_true",
                        help="Render with both backends and compare")
    parser.add_argument("--list-screens", action="store_true",
                        help="List screens in the document without rendering")
    args = parser.parse_args()

    # If no file given, render all fixtures
    if args.file is None:
        fixtures = find_fixtures()
        if not fixtures:
            print("No .nstudio fixtures found.")
            return 1
        print(f"Found {len(fixtures)} fixture(s). Rendering with {args.backend} backend...\n")
        for fixture in fixtures:
            print(f"--- {os.path.basename(fixture)} ---")
            _render_file(fixture, args)
            print()
        return 0

    return _render_file(args.file, args)


def _render_file(filepath, args):
    if not os.path.exists(filepath):
        print(f"ERROR: file not found: {filepath}")
        return 1

    doc = nstudio_load(filepath)
    basename = os.path.splitext(os.path.basename(filepath))[0]

    # List screens mode
    if args.list_screens:
        print(f"File: {filepath}")
        print(f"Version: {doc.version}")
        print(f"Screens: {len(doc.screens)}")
        for s in doc.screens:
            print(f"  - {s.id}: {s.size.get('width', '?')}x{s.size.get('height', '?')}")
        print(f"Components: {len(doc.component_ids())}")
        print(f"Behaviors: {len(doc.behaviors)}")
        print(f"Bindings: {len(doc.bindings)}")
        return 0

    # Determine output directory
    if args.output:
        if os.path.isdir(args.output) or args.output.endswith("/"):
            output_dir = args.output
        else:
            output_dir = os.path.dirname(args.output) or "."
    else:
        output_dir = os.path.join(".", "render_output", basename)
    os.makedirs(output_dir, exist_ok=True)

    # Render
    backends = ["pil", "sdl"] if args.compare else [args.backend]
    all_results = {}

    for backend in backends:
        if backend == "pil":
            results = render_pil(doc, output_dir, args.theme, args.scale, args.screen)
        elif backend == "sdl":
            results = render_sdl(doc, output_dir, args.theme, args.scale, args.screen)
        else:
            continue
        all_results[backend] = results

    # Print results
    for backend, results in all_results.items():
        if not results:
            continue
        print(f"Backend: {backend.upper()}")
        for screen_id, (w, h), size_bytes, elapsed in results:
            print(f"  {screen_id}: {w}x{h} ({size_bytes:,} bytes, {elapsed*1000:.1f}ms)")
        print(f"  Output: {output_dir}/")

    # Compare mode
    if args.compare and len(all_results) == 2:
        pil_results = {r[0]: r for r in all_results.get("pil", [])}
        sdl_results = {r[0]: r for r in all_results.get("sdl", [])}
        print("\nComparison:")
        for sid in pil_results:
            if sid in sdl_results:
                pil_s = pil_results[sid]
                sdl_s = sdl_results[sid]
                speedup = pil_s[3] / sdl_s[3] if sdl_s[3] > 0 else float('inf')
                print(f"  {sid}: PIL {pil_s[3]*1000:.1f}ms vs SDL {sdl_s[3]*1000:.1f}ms "
                      f"({speedup:.1f}x)")

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
