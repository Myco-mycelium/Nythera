#!/usr/bin/env python3
"""nst_validate — batch validator for .nstudio design documents.

The editing-time half of the IDE-integration story (M14 Phase 3): the
VS Code extension (ide/vscode-nyrqis/) execs this script to produce
LSP-shaped diagnostics for .nstudio files. Validation uses the
pure-Python floor (``ui/nstudio.loads``) — the SAME gate the daemon's
operator-only ``nui_validate`` op runs on import (ADR-0025), so what
the editor shows is what the import gate will enforce.

Why the floor and not the daemon: the import gate is operator-only by
design (ipc/transport.py's operator carve-out) — an editor would have
to hold operator authority to round-trip every keystroke. The floor is
the same validation logic with identical exception semantics, locally.

Output: one JSON array on stdout —
    [{"file": str, "line": int, "column": int,
      "severity": "error"|"warning", "code": str, "message": str}, ...]
Exit codes: 0 = all documents valid (possibly with warnings),
1 = at least one error diagnostic, 2 = usage/internal failure.

References: NFS-001 (NUI schema), ADR-0025 (import gate), ADR-0020
(floor/crate routing — the crate raises the same exceptions, so this
validator's behavior is engine-independent).
"""

import argparse
import json
import os
import sys
from typing import Any, Dict, List

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from ui import nstudio  # noqa: E402 (path bootstrap above)


def _diagnostic(file: str, exc: Exception, code: str) -> Dict[str, Any]:
    """One error diagnostic. The floor's exceptions carry messages but
    not positions, so the diagnostic anchors to the document head —
    honest for document-level contract violations."""
    return {
        "file": file,
        "line": 1,
        "column": 1,
        "severity": "error",
        "code": code,
        "message": str(exc),
    }


def validate_file(path: str) -> List[Dict[str, Any]]:
    """Validate one .nstudio file; returns its diagnostics (empty when
    valid). File-read failures are diagnostics too — the editor should
    see them, not a stderr tombstone."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        return [_diagnostic(path, exc, "io-error")]
    try:
        nstudio.loads(text)
    except nstudio.NstudioVersionError as exc:
        return [_diagnostic(path, exc, "schema-version")]
    except nstudio.NstudioValidationError as exc:
        return [_diagnostic(path, exc, "validation")]
    return []


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nst_validate",
        description="Validate .nstudio design documents with the same "
                    "gate the daemon's import path uses (ADR-0025)")
    parser.add_argument("files", nargs="+",
                        help=".nstudio files to validate")
    args = parser.parse_args(argv)

    diagnostics: List[Dict[str, Any]] = []
    for path in args.files:
        diagnostics.extend(validate_file(path))

    json.dump(diagnostics, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    has_error = any(d["severity"] == "error" for d in diagnostics)
    return 1 if has_error else 0


if __name__ == "__main__":
    sys.exit(main())
