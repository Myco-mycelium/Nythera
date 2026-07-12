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

Milestones 1–6 complete (Repository Bootstrap, Platform Constitution, Core
Architecture, Storage, Runtime, Security). Milestone 7 (Gaming Subsystem)
in progress. See [`REPOSITORY_STATE.md`](docs/00-platform/REPOSITORY_STATE.md)
and [`NPC-007 Project Roadmap`](docs/00-platform/007-PROJECT_ROADMAP.md).

All subsystems in [`NPC-008 Subsystem Owners`](docs/00-platform/SUBSYSTEM_OWNERS.md)
are currently unassigned pending contributors.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). All contributions are governed by
[`NPC-001 Project Constitution`](docs/00-platform/001-PROJECT_CONSTITUTION.md).

## License

See [`LICENSE`](LICENSE).
