# tools

This directory is scaffolded per docs/00-platform/003-ENGINEERING_HANDBOOK.md.

## Current state

- `check_depends_on_cycles.py` — verifies the `depends_on` graph across
  every document in `docs/00-platform/` and `docs/reference/` is a DAG
  (no circular references). Run with `python3 tools/check_depends_on_cycles.py`.
  Not yet wired into CI — see `REPOSITORY_STATE.md` Next Actions.
