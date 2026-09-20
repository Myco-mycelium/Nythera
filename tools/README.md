# tools

This directory is scaffolded per docs/00-platform/003-ENGINEERING_HANDBOOK.md.

## Gate tools

- `check_version_drift.py` — verifies the versions and dates agree
  across `pyproject.toml`, the backend CHANGELOG, and the git tags.
  Run with `python3 tools/check_version_drift.py`.
- `check_doc_premises.py` — re-verifies the repository's recorded
  factual premises ("evidence is BENCHMARK_RESULTS §32e", "the crate is
  shipped", "the rename is still pending") against current reality via
  an explicit CLAIM REGISTRY at the top of the file. This mechanizes the
  2026-09-20 premise audit, which caught two stale records by hand
  (ADR-0018's status cells; ADR-0009-review-package citing a
  "pending" benchmark that had existed for ten days). Run with
  `python3 tools/check_doc_premises.py`; `--list` prints the registry,
  `--json` emits machine-readable results. **Whenever a document records
  a new load-bearing "X exists / X is named Y" premise, add a registry
  entry for it.**
- `check_depends_on_cycles.py` — verifies the `depends_on` graph across
  every document in `docs/00-platform/` and `docs/reference/` is a DAG
  (no circular references). Run with `python3 tools/check_depends_on_cycles.py`.
  Runs in CI as the `Check depends_on cycles` step of
  `.github/workflows/docs.yml`, alongside the premise check above.

The generator/preview tooling (`generate_*.py`, `render.py`,
`preview_server.py`, `validate_generators.py`, `compare_benchmarks.py`)
is documented inline in each script.
