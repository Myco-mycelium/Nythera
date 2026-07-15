#!/usr/bin/env python3
"""
check_depends_on_cycles.py — verify docs/ front-matter depends_on graph is
a DAG (no circular references).

Why this exists: during threat model Phase 3, four real circular
dependencies were found in already-committed documents (e.g. NPS-001
listed ADR-0012 in depends_on, while ADR-0012 itself listed NPS-001 —
each was individually reasonable at the time it was added, but together
they formed a cycle nothing had caught). This script exists so that
doesn't happen silently again.

Usage:
    python3 tools/check_depends_on_cycles.py

Exits 0 with "No cycles found" if the graph is a DAG, exits 1 and prints
every cycle found otherwise.

Note: this checks the *formal* depends_on graph, not prose citations.
Convention established across this repository: a document MAY cite
another document that depends on it in prose (e.g. "per ADR-0012 §..."),
but MUST NOT list it in its own depends_on front-matter if that would
close a cycle. When in doubt, the earlier/more foundational document
should not depend on the later one that analyzes or amends it.
"""

import re
import glob
import sys


def build_graph(root: str = "docs") -> dict[str, list[str]]:
    files = glob.glob(f"{root}/00-platform/*.md") + glob.glob(
        f"{root}/reference/**/*.md", recursive=True
    )
    files = [f for f in files if not f.endswith("README.md")]

    graph: dict[str, list[str]] = {}
    for f in files:
        text = open(f, encoding="utf-8", errors="ignore").read()
        if not text.startswith("---"):
            continue
        fm = text.split("---", 2)[1]
        m = re.search(r"document_id:\s*(\S+)", fm)
        if not m:
            continue
        doc_id = m.group(1)
        d = re.search(r"depends_on:\s*\[([^\]]*)\]", fm)
        deps = [x.strip() for x in d.group(1).split(",") if x.strip()] if d else []
        graph[doc_id] = deps
    return graph


def find_cycles(graph: dict[str, list[str]]) -> list[list[str]]:
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in graph}
    cycles: list[list[str]] = []

    def dfs(node: str, path: list[str]) -> None:
        color[node] = GRAY
        for dep in graph.get(node, []):
            if dep not in graph:
                continue  # dependency on a document outside this graph (e.g. NTM-000 has none itself)
            if color.get(dep, WHITE) == GRAY:
                cycle_start = path.index(dep) if dep in path else 0
                cycles.append(path[cycle_start:] + [dep])
            elif color.get(dep, WHITE) == WHITE:
                dfs(dep, path + [dep])
        color[node] = BLACK

    for n in list(graph.keys()):
        if color[n] == WHITE:
            dfs(n, [n])
    return cycles


def main() -> int:
    graph = build_graph()
    cycles = find_cycles(graph)
    if cycles:
        for c in cycles:
            print("CYCLE:", " -> ".join(c))
        print(f"\n{len(cycles)} cycle(s) found across {len(graph)} documents.")
        return 1
    print(f"No cycles found across {len(graph)} documents.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
