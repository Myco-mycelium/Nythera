#!/usr/bin/env python3
"""
check_version_drift.py — fail CI when the packaged version and the
changelog disagree.

Why this exists: the backend's pyproject.toml sat at 0.22.0 through four
released streams (0.23.0 → 0.28.0) while the CHANGELOG, tags, and docs
moved on — noticed 2026-09-10, after v0.28.0 had already shipped with the
stale package version. This script exists so that doesn't happen silently
again.

What it checks:
1. source/nyhal-linux-backend/pyproject.toml `version` equals the newest
   `## [x.y.z]` heading in source/nyhal-linux-backend/CHANGELOG.md
   (hard failure — this is the drift that shipped).
2. Every `## [x.y.z] - YYYY-MM-DD` CHANGELOG heading has a matching git
   tag `vx.y.z` (warn-only; older entries may predate tagging practice).
3. The newest CHANGELOG date is not in the future (warn-only: clocks and
   timezones differ, but a future date is almost always a typo).

Usage:
    python3 tools/check_version_drift.py

Exits 0 if consistent, 1 with a clear message otherwise. CI: a cheap
early job (see .github/workflows/ci.yml `version-drift`).
"""

import datetime
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "source" / "nyhal-linux-backend" / "pyproject.toml"
CHANGELOG = ROOT / "source" / "nyhal-linux-backend" / "CHANGELOG.md"

_HEADING_RE = re.compile(
    r"^## \[(\d+\.\d+\.\d+)\](?:\s*-\s*(\d{4}-\d{2}-\d{2}))?\s*$", re.MULTILINE
)


def _load_versions():
    """Return (pyproject_version, [(version, date_or_None), ...] newest-first)."""
    pyproject_text = PYPROJECT.read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject_text, re.MULTILINE)
    if not m:
        raise SystemExit(f"FAIL: no `version = \"...\"` found in {PYPROJECT}")
    pyproject_version = m.group(1)

    changelog_text = CHANGELOG.read_text(encoding="utf-8")
    releases = _HEADING_RE.findall(changelog_text)
    if not releases:
        raise SystemExit(f"FAIL: no `## [x.y.z]` headings found in {CHANGELOG}")
    return pyproject_version, releases


def _git_tags():
    try:
        out = subprocess.run(
            ["git", "tag", "-l"], capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:  # shallow CI clone etc.
        print(f"WARN: could not list git tags ({exc}); skipping tag cross-check")
        return set()
    return {t.strip() for t in out.splitlines() if t.strip()}


def main() -> int:
    failures = []
    warnings = []

    pyproject_version, releases = _load_versions()
    newest_version, newest_date = releases[0]

    # 1. Hard check: pyproject must match the newest changelog entry.
    if pyproject_version != newest_version:
        failures.append(
            f"pyproject.toml version {pyproject_version} != newest CHANGELOG "
            f"release {newest_version}. Bump pyproject.toml in the same "
            f"commit as the CHANGELOG entry (this is exactly the drift that "
            f"shipped v0.28.0 at 0.22.0)."
        )

    # 2. Warn-only: every dated release should be tagged.
    tags = _git_tags()
    for version, date in releases:
        if date and f"v{version}" not in tags:
            warnings.append(f"CHANGELOG release {version} has no git tag v{version}")

    # 3. Warn-only: newest release date must not be in the future.
    if newest_date:
        try:
            release_day = datetime.date.fromisoformat(newest_date)
            if release_day > datetime.date.today():
                warnings.append(
                    f"newest CHANGELOG date {newest_date} is in the future "
                    f"(today is {datetime.date.today().isoformat()})"
                )
        except ValueError:
            warnings.append(f"newest CHANGELOG date {newest_date!r} is not ISO YYYY-MM-DD")

    for w in warnings:
        print(f"WARN: {w}")
    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print(
        f"OK: pyproject {pyproject_version} == newest CHANGELOG release "
        f"{newest_version} ({newest_date or 'no date'}); "
        f"{len(releases)} releases checked"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
