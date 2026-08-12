# source

This directory is scaffolded per
`docs/00-platform/003-ENGINEERING_HANDBOOK.md` (NPC-003 §5).

## Current state

- `nyhal-linux-backend/` — the first real code in this repository: an
  externally-contributed Linux Backend for the NyHAL contract (NPS-017),
  merged after a push conflict and independently verified before being
  documented here (`python3 -m pytest test_backend.py` passes 20/20).
  Its own status document ([`IMPLEMENTATION_STATUS.md`](nyhal-linux-backend/IMPLEMENTATION_STATUS.md),
  `IMPL-001`) self-rates as "Experimental Backend — Core Implementation
  Complete, Performance/Integration Work Pending," explicitly **not yet
  conformant** to NPS-017 §5: capability enforcement is tracked but not
  wired into seccomp/LSM, the NyFS FUSE integration is structural only,
  and no benchmarks exist. Threat model Phase 4 (NPS-022) confirmed that
  self-assessment against the code.
  - `poc-container/nyctr.py` — the original spike kept as a minimal
    reference: proves the most basic container primitive (namespace
    isolation + a cgroup memory/pid limit) on stock Linux. Superseded in
    scope by the fuller backend above.
  - See [`README_IMPLEMENTATION.md`](nyhal-linux-backend/README_IMPLEMENTATION.md)
    for the implementation's own overview.

Every other subsystem directory implied by the specification set (kernel,
storage, compatibility runtimes, gaming subsystem, AI subsystem) remains
unstarted. Per NPC-003 §5.1, each subsystem's code MUST link back to the
specification(s) it implements as work begins.
