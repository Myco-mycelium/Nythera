#!/usr/bin/env python3
"""variant_mix — compose one shell document from several variants.

The last design-language candidate: per-screen variant mixing. Both
shipped shell trees share an identical screen structure (the pill is a
pure restyle), so a mixed document is well-defined: **each screen comes
wholly from one source** — e.g. the stock desktop with the pill's lock
screen, or the reverse. Not a component-level merge; screens are the
unit, which keeps structure drift impossible by construction.

The header contract is computed from what the RESULT contains, not what
the inputs declared: ``requiresRegistry`` is the union over the base
document plus only those sources whose screens were actually selected.
A mix that takes zero overlay screens never inherits the overlay's
requirements. Schema-version mismatches between sources are refused.

CLI::

    python3 -m ui.variant_mix base.nstudio overlay.nstudio \
        --screens lock --out mixed.nstudio
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_HERE = str(Path(__file__).resolve().parent.parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _load_doc(source: Any) -> Dict[str, Any]:
    """Accept a path, a JSON string, or an already-parsed document."""
    if isinstance(source, dict):
        return source
    if isinstance(source, (str, Path)):
        text = str(source)
        if text.lstrip().startswith("{"):
            return json.loads(text)
        with open(text, "r", encoding="utf-8") as fh:
            return json.load(fh)
    raise TypeError("unsupported document source: %r" % (type(source),))


def _header(doc: Dict[str, Any]) -> List[str]:
    reqs = doc.get("requiresRegistry") or []
    return [str(r) for r in reqs]


def _dotted_key(version: str):
    """Dotted-numeric sort key ("1.10" > "1.9"); None when not dotted."""
    parts = str(version).split(".")
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return None


def mix_screens(base: Any, overlay: Any,
                screens: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Compose a new document: base + the named screens from overlay.

    ``screens=None`` means every overlay screen (a full overlay). The
    result declares the union of requirements actually carried by its
    content; both sources must share the document schema version.
    """
    base_doc = _load_doc(base)
    overlay_doc = _load_doc(overlay)

    base_version = base_doc.get("version")
    overlay_version = overlay_doc.get("version")
    if base_version != overlay_version:
        raise ValueError(
            "schema version mismatch: base %r vs overlay %r — refusing "
            "to mix documents for different registries" %
            (base_version, overlay_version))

    base_screens = {s.get("id"): s for s in base_doc.get("screens", [])}
    overlay_screens = {s.get("id"): s for s in overlay_doc.get("screens", [])}

    if screens is None:
        requested = list(overlay_screens)
    else:
        requested = list(screens)
    unknown = [s for s in requested if s not in overlay_screens]
    if unknown:
        raise ValueError(
            "overlay has no screen(s) %s — available: %s" %
            (unknown, sorted(overlay_screens)))
    missing_in_base = [s for s in requested if s not in base_screens]
    if missing_in_base:
        raise ValueError(
            "base has no screen(s) %s — mixing requires the same "
            "screen structure (variants are pure restyles)" % missing_in_base)

    mixed = json.loads(json.dumps(base_doc))  # deep copy, no shared refs
    for sid in requested:
        mixed_screens = mixed["screens"]
        for i, s in enumerate(mixed_screens):
            if s.get("id") == sid:
                mixed_screens[i] = json.loads(
                    json.dumps(overlay_screens[sid]))
                break

    # Header contract: union over what the RESULT contains — the base
    # always contributes, the overlay only when its screens were taken.
    union = sorted({*_header(base_doc),
                    *((_header(overlay_doc) if requested else []))},
                   key=lambda v: (_dotted_key(v) is None,
                                  _dotted_key(v) or (0,), v))
    union = [v for v in union if _dotted_key(v) is not None] or union
    if union:
        mixed["requiresRegistry"] = union
    else:
        mixed.pop("requiresRegistry", None)

    base_name = (base_doc.get("project") or {}).get("name", "base")
    overlay_name = (overlay_doc.get("project") or {}).get("name", "overlay")
    mixed.setdefault("project", {})["name"] = (
        "%s + %s mix on %s" % (base_name, overlay_name,
                               ",".join(requested) or "(none)"))
    return mixed


def main(argv: Optional[List[str]] = None) -> int:
    """CLI: compose a mixed document and print its Inspector verdict."""
    from ui.nyforge_bridge import NyforgeBridge

    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        print("usage: variant_mix <base.nstudio> <overlay.nstudio> "
              "[--screens ID ...] [--out FILE]", file=sys.stderr)
        return 2
    base_path, overlay_path = argv[0], argv[1]
    screens: List[str] = []
    out_path = None
    i = 2
    while i < len(argv):
        if argv[i] == "--screens":
            screens.append(argv[i + 1])
            i += 2
        elif argv[i] == "--out":
            out_path = argv[i + 1]
            i += 2
        else:
            print("unknown argument: %s" % argv[i], file=sys.stderr)
            return 2

    mixed = mix_screens(base_path, overlay_path,
                        screens or None)
    text = json.dumps(mixed, indent=1)

    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print("wrote %s" % out_path)

    report = NyforgeBridge(None).inspect_version(text=text)
    verdict = "WOULD DROP features" if report.get("anyDropped") \
        else "fully honored by this build"
    print("requires: %s" % (mixed.get("requiresRegistry") or "(none)"))
    print("Verdict: %s" % verdict)
    return 0 if report.get("ok") and not report.get("anyDropped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
