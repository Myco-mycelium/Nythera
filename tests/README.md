# tests

This directory is scaffolded per
`docs/00-platform/003-ENGINEERING_HANDBOOK.md` (NPC-003 §5.3: every
subsystem MUST have a test suite before its specification is marked
`Accepted` — the existing `Accepted` specifications predate implementation,
so this directory will grow as code lands).

## Current contents

- [`BENCHMARK_PLAN.md`](BENCHMARK_PLAN.md) — methodology for every pending
  benchmark (IPC latency, Zstd compression levels, token-bucket tuning,
  FUSE overhead). It defines **methodology, not results** — no fabricated
  numbers, per NPC-002 §5.2. All four benchmarks are currently Not
  Started, blocked on something runnable to measure.

## Conformance tests

The Linux Backend's own test suite lives with its implementation
(`source/nyhal-linux-backend/test_backend.py`, currently 20/20 passing);
per NPC-009 §7.4, test suites SHOULD reference the requirement ID(s) they
exercise once they exist.
