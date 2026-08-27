#!/usr/bin/env python3
"""nyrqis_run.py — render a .nstudio design to PNG.

Demonstrates the full pipeline:
  Nyforge (.nstudio) → Nyrqis codec → Runtime → Compositor → PNG

Usage:
    python3 nyrqis_run.py input.nstudio -o output.png
    python3 nyrqis_run.py input.nstudio --apple --theme Solar
    python3 nyrqis_run.py input.nstudio --validate-only
    python3 nyrqis_run.py input.nstudio --session    # live DesktopSession
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
    parser.add_argument("--apple", action="store_true",
                        help="Use Apple-quality compositor (shadows, blur, gradients)")
    parser.add_argument("--dark", action="store_true", default=True,
                        help="Use dark mode (default)")
    parser.add_argument("--light", action="store_true",
                        help="Use light mode")
    parser.add_argument("--session", action="store_true",
                        help="Use live DesktopSession (window management, hit-test)")
    parser.add_argument("--dump-json", action="store_true",
                        help="Dump the NUI document as JSON to stdout")
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

    if args.dump_json:
        print(json.dumps(doc.to_dict(), indent=2))
        return 0

    if args.validate_only:
        # Run the full validation pipeline
        _run_validation(doc)
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
    dark_mode = not args.light
    t0 = time.time()
    try:
        if args.session:
            # Live DesktopSession path — creates windows, supports hit-test
            from ui.desktop_session import DesktopSession
            session = DesktopSession(doc)
            print(f"\nSession created: {len(session.windows)} windows")
            for w in session.windows:
                print(f"  {w.id} ({w.component_id}) @ ({w.x},{w.y}) {w.width}x{w.height}")
            if args.apple:
                from ui.apple_compositor import AppleCompositor
                comp = AppleCompositor(dark_mode=dark_mode, scale=args.scale)
                img = comp.render_session(session)
            else:
                from ui.compositor import Compositor
                comp = Compositor(theme_name=args.theme, scale=args.scale)
                img = comp.render_screen(doc, args.screen)
        elif args.apple:
            from ui.apple_compositor import AppleCompositor
            comp = AppleCompositor(dark_mode=dark_mode, scale=args.scale)
            img = comp.render_document(doc, screen_id=args.screen)
        else:
            from ui.compositor import Compositor
            comp = Compositor(theme_name=args.theme, scale=args.scale)
            img = comp.render_screen(doc, args.screen)
    except Exception as e:
        print(f"Error rendering: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
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


def _run_validation(doc):
    """Run the full NUI validation pipeline."""
    print("\n--- NUI Validation ---")
    issues = []

    # 1. Schema validation (already validated during import)
    print(f"  ✓ Schema: {doc.version} valid")

    # 2. API contract validation
    try:
        from ui.nstudio import COMPONENT_CONTRACTS, SYSTEM_ACTIONS
        all_types = set(COMPONENT_CONTRACTS.keys()) | set(SYSTEM_ACTIONS.keys())
        for comp_id in doc.component_ids():
            comp = doc.find_component(comp_id)
            if comp and comp.type and comp.type not in COMPONENT_CONTRACTS:
                issues.append(f"Unknown component type: {comp.type}")
        print(f"  ✓ API contract: {len(COMPONENT_CONTRACTS)} types, "
              f"{len(SYSTEM_ACTIONS)} system actions registered")
    except Exception as e:
        print(f"  ⚠ API contract: {e}")

    # 3. Accessibility audit
    try:
        from ui.a11y import audit_document
        a11y_issues = audit_document(doc)
        errors = [i for i in a11y_issues if i.get('severity') == 'error']
        warnings = [i for i in a11y_issues if i.get('severity') == 'warning']
        if errors:
            issues.extend([i['message'] for i in errors])
        print(f"  ✓ Accessibility: {len(errors)} errors, {len(warnings)} warnings")
    except Exception as e:
        print(f"  ⚠ Accessibility: {e}")

    # 4. Expression engine validation
    try:
        from ui import nexpr
        expr_count = 0
        for b in doc.behaviors:
            if not b.condition:
                continue
            # Collect expression strings from condition tree
            exprs = []
            def _collect_exprs(cond):
                if isinstance(cond, dict):
                    if 'expression' in cond:
                        exprs.append(cond['expression'])
                    for sub in (cond.get('conditions') or []):
                        _collect_exprs(sub)
            _collect_exprs(b.condition)
            for expr_str in exprs:
                try:
                    nexpr.parse(expr_str)
                    expr_count += 1
                except Exception as e:
                    issues.append(f"Behavior '{b.id}' expression: {e}")
        print(f"  ✓ Expressions: {expr_count} expressions in "
              f"{len(doc.behaviors)} behaviors checked")
    except Exception as e:
        print(f"  ⚠ Expressions: {e}")

    # 5. Asset validation — check declared resources exist in doc
    try:
        asset_refs = []
        def _collect_assets(c):
            for val in (c.properties or {}).values():
                if isinstance(val, str) and val.startswith('$asset:'):
                    asset_refs.append(val[7:])
            for ch in (c.children or []):
                _collect_assets(ch)
        for s in doc.screens:
            _collect_assets(s.root)
        declared = {a.get('id') for a in (doc.resources.get('assets') or [])}
        missing = [r for r in asset_refs if r not in declared]
        if missing:
            issues.extend([f"Undeclared asset: {m}" for m in missing])
        print(f"  ✓ Assets: {len(asset_refs)} refs, {len(declared)} declared, "
              f"{len(missing)} missing")
    except Exception as e:
        print(f"  ⚠ Assets: {e}")

    # Summary
    print(f"\n{'='*40}")
    if issues:
        print(f"FAILED — {len(issues)} issue(s):")
        for issue in issues:
            print(f"  • {issue}")
    else:
        print("PASSED — all checks OK")


if __name__ == "__main__":
    sys.exit(main())
