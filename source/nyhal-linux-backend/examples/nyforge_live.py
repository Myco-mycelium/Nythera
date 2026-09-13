#!/usr/bin/env python3
"""nyforge_live — Live Nyforge preview on the Nyrqis desktop.

Loads a .nstudio file from Nyforge and renders it live on the
Nyrqis desktop session.  Supports hot-reload so changes in
Nyforge appear instantly.

Usage::

    # One-shot load
    python3 nyforge_live.py path/to/design.nstudio

    # Hot-reload mode (reloads on file change)
    python3 nyforge_live.py --watch path/to/design.nstudio

    # Load and render to PNG
    python3 nyforge_live.py --render output.png path/to/design.nstudio

References:
    - NFS-001 §3: NUI layout system
    - doc #14: Nyrqis Desktop Shell
    - ADR-0025 §9: runtime consumption decision
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

# Ensure the backend root is on the path
_backend = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from ui.desktop_session import DesktopSession
from ui.nyforge_bridge import NyforgeBridge


def build_side_panel(nstudio_path: str, output_path: str,
                     theme: str = "Eclipse") -> Dict[str, Any]:
    """Render the document preview + Inspector side panel to a PNG.

    The editor's companion: the picture Nyforge is about to present,
    beside the contract verdict from ``inspect_version``. Returns a
    small dict summary (paths + verdict), suitable for --json output.
    """
    from ui.inspector_panel import compose_inspector_view
    from ui.nyforge_bridge import NyforgeBridge as _Bridge

    inspector = _Bridge(None)
    report = inspector.inspect_version(path=nstudio_path)

    preview = None
    if report.get("ok"):
        try:
            from ui.nstudio import load
            session = DesktopSession(load(nstudio_path))
            preview = session.live_render()
        except Exception:
            preview = None

    img = compose_inspector_view(preview, report, theme=theme)
    img.save(output_path)
    return {
        "panel": output_path,
        "verdict": ("WOULD DROP features" if report.get("anyDropped")
                    else ("fully honored by this build"
                          if report.get("ok") else "error")),
        "preview_rendered": preview is not None,
        "panel_size": list(img.size),
    }


def make_session_from_nstudio(path: str) -> tuple:
    """Create a DesktopSession from an .nstudio file.

    Returns (session, bridge, result).
    """
    from ui.nstudio import load

    doc = load(path)
    session = DesktopSession(doc)
    bridge = NyforgeBridge(session)
    result = bridge.load_document(path)
    return session, bridge, result


def main():
    parser = argparse.ArgumentParser(
        description="Nyforge → Nyrqis live preview",
    )
    parser.add_argument(
        "nstudio",
        help="Path to .nstudio file",
    )
    parser.add_argument(
        "--watch", "-w",
        action="store_true",
        help="Enable hot-reload on file changes",
    )
    parser.add_argument(
        "--interval", "-i",
        type=float,
        default=1.0,
        help="Hot-reload poll interval (seconds)",
    )
    parser.add_argument(
        "--render", "-r",
        metavar="OUTPUT.png",
        help="Render to PNG and exit",
    )
    parser.add_argument(
        "--side-panel", "-s",
        metavar="OUTPUT.png",
        help="Render the document preview + Inspector side panel "
             "(verdict panel beside the preview) and exit",
    )
    parser.add_argument(
        "--theme",
        metavar="NAME",
        default="Eclipse",
        help="Side-panel theme (Eclipse/Solar)",
    )
    parser.add_argument(
        "--inspect",
        action="store_true",
        help="Report the document's contract/version situation against "
             "the registry versionHistory (Inspector preflight) and exit",
    )
    parser.add_argument(
        "--json", "-j",
        action="store_true",
        help="Output summary as JSON",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose logging",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )

    if not os.path.exists(args.nstudio):
        print(f"Error: file not found: {args.nstudio}", file=sys.stderr)
        sys.exit(1)

    # Inspector side panel: preview + verdict beside it, one PNG.
    if args.side_panel:
        try:
            summary = build_side_panel(args.nstudio, args.side_panel,
                                       theme=args.theme)
        except Exception as exc:
            print(f"Error: side panel failed: {exc}", file=sys.stderr)
            sys.exit(1)
        if args.json:
            print(json.dumps(summary, indent=2))
        else:
            print(f"Side panel: {summary['panel']} "
                  f"({summary['panel_size'][0]}x{summary['panel_size'][1]})")
            print(f"Preview rendered: {summary['preview_rendered']}")
            print(f"Verdict: {summary['verdict']}")
        return

    # Inspector preflight: report the document's version situation
    # without opening it.
    if args.inspect:
        bridge = NyforgeBridge(None)
        report = bridge.inspect_version(path=args.nstudio)
        if not report.get("ok"):
            print(f"Error: {report.get('error', 'unknown')}", file=sys.stderr)
            sys.exit(1)
        print(f"Document schema version: "
              f"{report['documentSchemaVersion']} "
              f"(supported: {report['schemaSupported']})")
        print(f"Registry requirements: "
              f"{report['docHeaderVersions'] or '(none declared)'}")
        if report["changesSinceOldestRequirement"]:
            print("Contract changes covered:")
            for entry in report["changesSinceOldestRequirement"]:
                print(f"  {entry['registryVersion']}: {entry['change']}")
        if report["notYetInRegistry"]:
            print(f"Requires a newer registry than this build: "
                  f"{report['notYetInRegistry']}")
        if report["unknownDocRequirements"]:
            print(f"Unrecognized requirements (review): "
                  f"{report['unknownDocRequirements']}")
        verdict = "WOULD DROP features" if report["anyDropped"] \
            else "fully honored by this build"
        print(f"Verdict: {verdict}")
        return

    # Load and bridge
    print(f"Loading {args.nstudio}...")
    session, bridge, result = make_session_from_nstudio(args.nstudio)

    if not result["ok"]:
        print(f"Error: {result.get('error', 'unknown')}", file=sys.stderr)
        sys.exit(1)

    print(
        f"Bridged {result['windows_created']} windows, "
        f"{result['behaviors_wired']} behaviors, "
        f"{result['bindings_applied']} bindings"
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        summary = bridge.summary()
        print(f"Document: {summary['doc_path']}")
        print(f"Hash: {summary['doc_hash']}")
        print(f"Mapped windows: {summary['mapped_windows']}")
        for cid, w in summary["windows"].items():
            print(f"  {cid}: {w['role']} ({w['size']}) — {w['title']}")

    # Render to PNG
    if args.render:
        print(f"Rendering to {args.render}...")
        img = session.live_render()
        if img is not None:
            img.save(args.render)
            print(f"Saved: {args.render} ({img.size[0]}x{img.size[1]})")
        else:
            print("Warning: render returned None", file=sys.stderr)
        return

    # Hot-reload mode
    if args.watch:
        print(f"Hot-reload enabled (interval={args.interval}s)")

        # In watch mode the side panel becomes a live companion: every
        # accepted reload re-renders the verdict next to the new
        # preview. Rejections leave the last good panel on disk (the
        # session is still running it).
        side_panel_png = args.side_panel

        def on_reload(event, data):
            print(f"[reload] {event}: {json.dumps(data, indent=2)}")
            if side_panel_png and event == "reload":
                try:
                    build_side_panel(args.nstudio, side_panel_png,
                                     theme=args.theme)
                except Exception as exc:
                    print(f"[side-panel] re-render failed: {exc}",
                          file=sys.stderr)

        bridge.enable_hot_reload(
            interval=args.interval,
            callback=on_reload,
        )

        print("Watching for changes... (Ctrl+C to stop)")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nStopping...")
            bridge.disable_hot_reload()
            return

    # Interactive mode
    print("\nDesktop session active. Session summary:")
    summary = session.summary()
    for key, value in summary.items():
        if key == "windows":
            continue
        print(f"  {key}: {value}")

    print(f"\nWindows ({len(summary.get('windows', []))}):")
    for w in summary.get("windows", []):
        print(f"  [{w.get('id', '?')}] {w.get('title', '?')} "
              f"({w.get('width', 0)}x{w.get('height', 0)})")


if __name__ == "__main__":
    main()
