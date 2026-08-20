#!/usr/bin/env python3
"""nyrqis_run.py — render a .nstudio design to PNG.

Demonstrates the full pipeline:
  Nyforge (.nstudio) → Nyrqis codec → Runtime → Compositor → PNG

Usage:
    python3 nyrqis_run.py input.nstudio -o output.png
    python3 nyrqis_run.py input.nstudio --screen desktop --theme Solar
    python3 nyrqis_run.py input.nstudio --validate-only
"""

import argparse
import json
import os
import sys
import time

# Ensure the backend package is importable
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def main():
    parser = argparse.ArgumentParser(
        description="Render a Nyrqis .nstudio design to PNG",
    )
    parser.add_argument("input", help="Path to .nstudio file")
    parser.add_argument("-o", "--output", default=None,
                        help="Output PNG path (default: <input>.png)")
    parser.add_argument("--screen", default=None,
                        help="Screen ID to render (default: first screen)")
    parser.add_argument("--theme", default="Eclipse",
                        choices=["Eclipse", "Solar"],
                        help="Theme to use")
    parser.add_argument("--scale", type=float, default=1.0,
                        help="Render scale (1.0 = native, 2.0 = retina)")
    parser.add_argument("--validate-only", action="store_true",
                        help="Validate the document without rendering")
    parser.add_argument("--summary", action="store_true",
                        help="Print runtime summary")
    parser.add_argument("--interactive", action="store_true",
                        help="Enable interactive mode (fire events)")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: {args.input} not found", file=sys.stderr)
        return 1

    # Load the document
    from ui.nstudio import load
    t0 = time.time()
    try:
        doc = load(args.input)
    except Exception as e:
        print(f"Error loading {args.input}: {e}", file=sys.stderr)
        return 1
    t_load = time.time() - t0

    # Print summary
    project = doc.project or {}
    print(f"Loaded: {project.get('name', 'unnamed')} v{project.get('version', '?')}")
    print(f"  Screens: {len(doc.screens)}")
    print(f"  Components: {_count_components(doc)}")
    print(f"  Behaviors: {len(doc.behaviors)}")
    print(f"  Bindings: {len(doc.bindings)}")
    print(f"  States: {len(doc.states)}")
    print(f"  Animations: {len(doc.animations)}")
    print(f"  Theme: {doc.themes.get('active', 'Eclipse')}")
    print(f"  Load time: {t_load*1000:.1f}ms")

    if args.validate_only:
        print("\n✓ Document is valid")
        return 0

    # Run the runtime (apply bindings, etc.)
    from ui.runtime import NyrqisRuntime
    rt = NyrqisRuntime(doc)
    rt_summary = rt.summary()
    if args.summary:
        print(f"\nRuntime summary:")
        for k, v in rt_summary.items():
            print(f"  {k}: {v}")

    # Render to PNG
    from ui.compositor import Compositor
    compositor = Compositor(
        theme_name=args.theme,
        scale=args.scale,
    )

    t0 = time.time()
    try:
        img = compositor.render_screen(doc, args.screen)
    except Exception as e:
        print(f"Error rendering: {e}", file=sys.stderr)
        return 1
    t_render = time.time() - t0

    # Determine output path
    output = args.output
    if output is None:
        base = os.path.splitext(os.path.basename(args.input))[0]
        output = f"{base}.png"

    img.save(output)
    print(f"\n✓ Rendered to {output}")
    print(f"  Size: {img.size[0]}x{img.size[1]}")
    print(f"  Render time: {t_render*1000:.1f}ms")
    print(f"  Theme: {args.theme}")
    print(f"  Scale: {args.scale}x")

    return 0


def _count_components(doc):
    """Count total components across all screens."""
    count = 0
    for screen in doc.screens:
        if screen.root:
            count += _count_tree(screen.root)
    count += len(doc.component_ids())
    return count


def _count_tree(node, depth=0):
    """Count a component tree recursively (with depth limit)."""
    if depth > 50:
        return 1
    count = 1
    children = getattr(node, 'children', []) or []
    for child in children:
        count += _count_tree(child, depth + 1)
    return count


if __name__ == "__main__":
    sys.exit(main())
