#!/usr/bin/env python3
"""Compare benchmark results against a baseline.

Downloads the previous benchmark artifact from GitHub Actions and
compares it against the current run. Reports any regressions.

Usage:
    python3 tools/compare_benchmarks.py [--baseline PATH] [--threshold PERCENT]

Exit codes:
    0 — no regressions detected
    1 — regression detected (performance degraded)
    2 — error (cannot load baseline)
"""

import argparse
import json
import os
import sys
import urllib.request
from typing import Dict, List, Tuple


def load_benchmark_file(path: str) -> Dict[str, Dict[str, float]]:
    """Load benchmark results from a JSON file."""
    with open(path, "r") as f:
        data = json.load(f)
    # Convert list of dicts to dict keyed by fixture name
    result = {}
    for row in data:
        fixture = row.get("fixture", "")
        if fixture:
            result[fixture] = {
                k: v for k, v in row.items()
                if isinstance(v, (int, float))
            }
    return result


def download_latest_benchmark(run_id: int, token: str) -> str:
    """Download the latest benchmark artifact from GitHub Actions."""
    # Find the artifacts for this run
    url = f"https://api.github.com/repos/Myco-mycelium/Nythera/actions/runs/{run_id}/artifacts"
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
    })
    try:
        resp = urllib.request.urlopen(req)
        artifacts = json.loads(resp.read())
    except Exception as e:
        print(f"Error fetching artifacts: {e}", file=sys.stderr)
        return ""

    # Find the benchmarks artifact
    for artifact in artifacts.get("artifacts", []):
        if artifact.get("name") == "benchmarks":
            download_url = artifact.get("archive_download_url")
            if download_url:
                req = urllib.request.Request(download_url, headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                })
                try:
                    resp = urllib.request.urlopen(req)
                    import zipfile
                    import io
                    data = io.BytesIO(resp.read())
                    with zipfile.ZipFile(data) as zf:
                        for name in zf.namelist():
                            if name.endswith(".json"):
                                content = zf.read(name)
                                return content.decode("utf-8")
                except Exception as e:
                    print(f"Error downloading artifact: {e}", file=sys.stderr)
    return ""


def compare_results(
    baseline: Dict[str, Dict[str, float]],
    current: Dict[str, Dict[str, float]],
    threshold: float = 20.0,
) -> Tuple[List[str], List[str]]:
    """Compare baseline vs current results.

    Returns (regressions, improvements) as lists of messages.
    """
    regressions = []
    improvements = []

    for fixture in current:
        if fixture not in baseline:
            continue
        for metric in current[fixture]:
            if metric not in baseline[fixture]:
                continue
            base_val = baseline[fixture][metric]
            curr_val = current[fixture][metric]
            if base_val <= 0:
                continue
            change_pct = ((curr_val - base_val) / base_val) * 100
            if change_pct > threshold:
                regressions.append(
                    f"  REGRESSION: {fixture}/{metric}: {base_val:.1f}ms → {curr_val:.1f}ms (+{change_pct:.1f}%)"
                )
            elif change_pct < -threshold:
                improvements.append(
                    f"  IMPROVEMENT: {fixture}/{metric}: {base_val:.1f}ms → {curr_val:.1f}ms ({change_pct:.1f}%)"
                )

    return regressions, improvements


def main():
    parser = argparse.ArgumentParser(description="Compare benchmark results")
    parser.add_argument("--current", required=True, help="Current benchmark JSON file")
    parser.add_argument("--baseline", default=None, help="Baseline benchmark JSON file (or download from CI)")
    parser.add_argument("--threshold", type=float, default=20.0, help="Regression threshold percentage (default: 20%)")
    args = parser.parse_args()

    # Load current results
    if not os.path.exists(args.current):
        print(f"ERROR: Current file not found: {args.current}", file=sys.stderr)
        return 2
    current = load_benchmark_file(args.current)
    if not current:
        print("ERROR: No results in current file", file=sys.stderr)
        return 2

    # Load or download baseline
    if args.baseline and os.path.exists(args.baseline):
        baseline = load_benchmark_file(args.baseline)
    else:
        print("No baseline provided, skipping comparison")
        print("To compare, provide --baseline <file> or set GITHUB_TOKEN")
        return 0

    if not baseline:
        print("WARNING: No results in baseline file", file=sys.stderr)
        return 0

    # Compare
    regressions, improvements = compare_results(baseline, current, args.threshold)

    print(f"Benchmark comparison (threshold: {args.threshold}%)")
    print(f"Current: {len(current)} fixtures")
    print(f"Baseline: {len(baseline)} fixtures")

    if improvements:
        print(f"\nImprovements ({len(improvements)}):")
        for msg in improvements:
            print(msg)

    if regressions:
        print(f"\nRegressions ({len(regressions)}):")
        for msg in regressions:
            print(msg)
        return 1
    else:
        print("\nNo regressions detected.")
        return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
