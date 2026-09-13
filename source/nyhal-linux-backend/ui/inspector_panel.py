#!/usr/bin/env python3
"""inspector_panel — render a document's contract/version report as UI.

The visual half of the Inspector preflight: ``NyforgeBridge
.inspect_version`` produces the machine-readable verdict (what a
.nstudio document requires, what this build would drop); this module
renders the same report as pixels, two ways:

- **Compositor path** — a gate-valid NUI ``.nstudio`` document whose
  Text components carry the verdict (rendered by the real compositor
  on either brand theme, so the panel is a real shell surface);
- **Plain path** — the same lines as an RGBA PIL image with the
  same theme palette, for embedding where a document is not needed.

Both paths render identical *content*; the tests pin that.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image, ImageDraw

_HERE = str(Path(__file__).resolve().parent.parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from ui.compositor import THEMES  # noqa: E402

# Panel geometry (§4 targets: rows are 28 px, >= 8 px separation).
PANEL_WIDTH = 560
ROW_HEIGHT = 28
PANEL_MARGIN = 16
PADDING = 12

_VERDICT_BAD = "WOULD DROP features"
_VERDICT_OK = "fully honored by this build"


def report_lines(report: Dict[str, Any]) -> List[str]:
    """Flatten an ``inspect_version`` report into the panel's lines.

    The single content definition: the compositor document and the
    plain RGBA render both show exactly these lines.
    """
    lines: List[str] = ["Inspector preflight", ""]
    if not report.get("ok", False):
        lines.append("error: " + str(report.get("error", "unknown")))
        return lines

    lines.append(
        "schema %s (supported: %s)" % (
            report.get("documentSchemaVersion"),
            "yes" if report.get("schemaSupported") else "NO"))
    reqs = report.get("docHeaderVersions") or []
    lines.append("requires: %s" % (", ".join(reqs) if reqs else "(none)"))
    lines.append("")

    for entry in report.get("changesSinceOldestRequirement") or []:
        lines.append("%s: %s" % (
            entry.get("registryVersion"), entry.get("change", "")))
    if report.get("changesSinceOldestRequirement"):
        lines.append("")

    if report.get("notYetInRegistry"):
        lines.append("Newer than this build (will be dropped):")
        lines.append("  " + ", ".join(report["notYetInRegistry"]))
    if report.get("unknownDocRequirements"):
        lines.append("Unrecognized requirements (review):")
        lines.append("  " + ", ".join(report["unknownDocRequirements"]))

    lines.append("")
    lines.append("Verdict: " + (
        _VERDICT_BAD if report.get("anyDropped") else _VERDICT_OK))
    return lines


def build_inspector_document(report: Dict[str, Any],
                             screen_width: int = PANEL_WIDTH) -> Dict[str, Any]:
    """Build a gate-valid NUI document showing the report.

    Every line becomes a Text component on a Window; the document
    passes ``ui.nstudio.load`` (the real import gate) — the panel is a
    real shell surface, not a picture of one.
    """
    lines = report_lines(report)

    def text_comp(idx: int, line: str) -> Dict[str, Any]:
        return {
            "id": "inspector_line_%d" % idx,
            "type": "Text",
            "properties": {"text": line if line else " "},
            "layout": {
                "x": PADDING,
                "y": PADDING + idx * ROW_HEIGHT,
                "width": screen_width - 2 * PADDING,
                "height": ROW_HEIGHT,
            },
            "events": {},
            "children": [],
        }

    height = PADDING * 2 + len(lines) * ROW_HEIGHT
    children = [text_comp(i, ln) for i, ln in enumerate(lines)]
    return {
        "version": "1.0.0",
        "project": {
            "name": "Nyforge Inspector preflight",
            "id": "nyforge-inspector-preflight",
        },
        "themes": {"active": "Eclipse", "overrides": {}},
        "screens": [{
            "id": "inspector",
            "size": {"width": screen_width, "height": height},
            "root": {
                "id": "inspector_window",
                "type": "Window",
                "properties": {
                    "title": "Inspector preflight",
                    "width": screen_width,
                    "height": height,
                },
                "layout": {"x": 0, "y": 0, "width": screen_width,
                           "height": height},
                "events": {},
                "children": children,
            },
        }],
    }


def render_inspector_panel(report: Dict[str, Any],
                           theme: str = "Eclipse") -> Image.Image:
    """Render the report as RGBA pixels (no document involved).

    Same content as :func:`build_inspector_document` (pinned by tests);
    same palette as the compositor's brand themes.
    """
    lines = report_lines(report)
    palette = THEMES.get(theme) or THEMES["Eclipse"]

    height = PADDING * 2 + len(lines) * ROW_HEIGHT
    img = Image.new("RGBA", (PANEL_WIDTH, height), palette["background"])
    draw = ImageDraw.Draw(img)
    y = PADDING
    for ln in lines:
        color = palette["text_primary"]
        if ln.startswith("Verdict:"):
            # Solid status badge behind the verdict: exact geometry (no
            # antialiasing), HIG-style emphasis, honest color coding.
            badge = palette["accent"] if _VERDICT_OK in ln \
                else (220, 80, 80)
            draw.rectangle(
                (PADDING - 4, y - 4, PANEL_WIDTH - PADDING + 4,
                 y + ROW_HEIGHT - 8), fill=badge)
            color = (255, 255, 255)
        elif ln and ln[0].isdigit():
            color = palette["text_secondary"]
        elif ln.startswith(("Newer than", "Unrecognized")):
            color = (220, 80, 80)
        draw.text((PADDING, y), ln if ln else " ", fill=color)
        y += ROW_HEIGHT
    return img


def compose_inspector_view(preview: Optional[Image.Image],
                           report: Dict[str, Any],
                           theme: str = "Eclipse",
                           gap: int = 24) -> Image.Image:
    """Compose the side-panel view: document preview + Inspector panel.

    The preview sits on the left, the verdict panel on the right — the
    "read the contract before you trust the picture" pairing. A
    ``None`` preview (document did not import) degrades honestly to the
    panel alone: there is nothing to preview, and the panel says why.
    """
    panel_img = render_inspector_panel(report, theme=theme)
    if preview is None:
        return panel_img
    palette = THEMES.get(theme) or THEMES["Eclipse"]
    width = preview.width + gap + PANEL_WIDTH
    height = max(preview.height, panel_img.height)
    canvas = Image.new("RGBA", (width, height), palette["background"])
    canvas.paste(preview, (0, 0))
    canvas.paste(panel_img, (preview.width + gap, 0))
    return canvas


def main(argv: Optional[List[str]] = None) -> int:
    """CLI: ``python3 -m ui.inspector_panel <document.nstudio>``."""
    from ui.nyforge_bridge import NyforgeBridge

    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: inspector_panel <document.nstudio> [--theme NAME]",
              file=sys.stderr)
        return 2
    path = argv[0]
    theme = "Eclipse"
    if "--theme" in argv:
        theme = argv[argv.index("--theme") + 1]

    bridge = NyforgeBridge(None)
    report = bridge.inspect_version(path=path)
    doc = build_inspector_document(report)
    import ui.nstudio as nstudio
    gate = nstudio.loads(json.dumps(doc))
    img = render_inspector_panel(report, theme=theme)
    img.save(path.rsplit(".", 1)[0] + "-inspector.png")
    print("document %s validated; panel: %s" % (
        gate.screens[0].id, path.rsplit(".", 1)[0] + "-inspector.png"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
