# source

This directory is scaffolded per docs/00-platform/003-ENGINEERING_HANDBOOK.md.

## Current state

- `nyhal-linux-backend/` — the first real code in this repository: a
  tested proof-of-concept for the container primitive described in
  NPS-017 §4.1. Not production code, not a working backend — see its
  README for exact scope.

Every other subsystem directory implied by the specification set
(kernel, storage, compatibility runtimes, gaming subsystem, AI subsystem)
remains unstarted. Per NPC-003 §5.1, each subsystem's code MUST link back
to the specification(s) it implements as work begins.
