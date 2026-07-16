# Nythera

> Build a platform worthy of the decades ahead.

Nythera is a from-scratch operating system project targeting native
performance across desktops, laptops, tablets, phones, handhelds, and
consoles, with compatibility layers for Windows (`.exe`/`.msi`) and Android
(`.apk`) applications, and a first-class gaming subsystem built on
compressed, mountable game disk images.

This repository is the canonical source of truth for Nythera's design,
governance, and (eventually) implementation.

## Start Here

1. **[The Nythera Manifest](docs/00-platform/000-THE_NYTHERA_MANIFEST.md)** —
   why this project exists and what it will never become.
2. **[Project Constitution](docs/00-platform/001-PROJECT_CONSTITUTION.md)** —
   the enforceable rules that govern the platform.
3. **[Repository State](docs/00-platform/REPOSITORY_STATE.md)** — what
   currently exists, at a glance.
4. **[Specification Index](docs/00-platform/004-SPECIFICATION_INDEX.md)** —
   the master index of every canonical document.

## Documentation

Documentation follows the [Diátaxis](https://diataxis.fr/) framework under
`docs/`:

| Folder | Purpose |
|--------|---------|
| `docs/00-platform/` | Foundational governance (Manifest, Constitution, handbook, indices) |
| `docs/tutorials/` | Learning-oriented, step-by-step guides |
| `docs/how-to/` | Task-oriented guides for a specific problem |
| `docs/reference/` | Precise technical reference (ADRs, NPS, ABI, API, package format) |
| `docs/explanation/` | Design rationale and context |

## Repository Layout

```
Nythera/
├── docs/            # All documentation (Diátaxis)
├── source/          # Kernel, subsystems, runtime code
├── tools/           # Build tooling, CLI utilities
├── tests/           # Conformance, integration, benchmark tests
├── sdk/             # Developer SDK and templates
├── examples/        # Example applications
└── engineering/     # RFC drafts, working notes, meeting records
```

See [`NPC-003 Engineering Handbook`](docs/00-platform/003-ENGINEERING_HANDBOOK.md)
for the full standard.

## Project Status

Milestones 1–11 complete (Repository Bootstrap through a response to an
external repository review — see `007-PROJECT_ROADMAP.md` for the full
history), plus a substantial, externally-contributed Linux Backend
implementation (`source/nyhal-linux-backend/`) — merged mid-session after
a push conflict, independently verified (`pytest` 20/20 passing) rather
than taken on faith. Milestone 11's structural recommendations are
resolved: a Requirements Database
(`NPC-009` + `docs/reference/requirements/REQUIREMENTS.md`) built per the
external review's own top priority, and a proposed NPS domain-renumbering
scheme formally **rejected** via `ADR-0017` rather than silently adopted.

Milestone 12 (the security threat model) is in progress, built in
explicit phases — Phases 1–4 are complete: methodology, attack surface
enumeration, STRIDE analysis, and privilege/escalation analysis. Phase 4
was the first phase analyzed against real code rather than a
hypothetical, and it found the most severe issue in the threat model to
date: the Linux Backend's capability enforcement covers exactly one
operation class (IPC send/call) — direct syscalls are completely
unmediated. `NPS-017` was tightened to require both control-plane and
data-plane enforcement, and the implementation is formally flagged
non-conformant against that requirement rather than the gap being
smoothed over. Across Phases 2–4, 12 findings have surfaced with a
disposition each — spec amendments, new ADRs, new requirements, or an
explicit "not fixable by amendment, tracked elsewhere." See
[`docs/reference/security/README.md`](docs/reference/security/README.md)
for the full phase plan. Milestone 11's other 9 gap categories (diagrams,
API/ABI reference, full object registry, and more) remain logged in
priority order, not yet
built. See
[`REPOSITORY_STATE.md`](docs/00-platform/REPOSITORY_STATE.md) and
[`NPC-007 Project Roadmap`](docs/00-platform/007-PROJECT_ROADMAP.md).

All subsystems in [`NPC-008 Subsystem Owners`](docs/00-platform/SUBSYSTEM_OWNERS.md)
are currently unassigned pending contributors.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). All contributions are governed by
[`NPC-001 Project Constitution`](docs/00-platform/001-PROJECT_CONSTITUTION.md).

## License

See [`LICENSE`](LICENSE).
