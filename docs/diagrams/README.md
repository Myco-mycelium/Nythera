# Diagrams

Architecture diagrams stored as **source** (Mermaid), per NPC-003 §4.3 —
never as embedded binary images without a source file. They render both
on GitHub and in the MkDocs site.

| Diagram | Shows | Governed by |
|---------|-------|-------------|
| [Boot Sequence](boot-sequence.md) | The six boot stages, firmware handoff to user session | NPS-001 §5 |
| [NyHAL Architecture](nyhal-architecture.md) | The four-layer stack and what depends on what | NPS-017 §3 |
| [Container Lifecycle](container-lifecycle.md) | The container state machine | NPS-010 §4 |
| [Capability Grant Flow](capability-grant-flow.md) | How a manifest request becomes a grant | NPS-010 §4.2, NPS-011 |
| [Capability Graph](capability-graph.md) | The permission structure: classes, instances, kernel arbitration, audit | NPS-011, NPS-010 §4–§6, NPS-003 §5 |
| [Object Graph](object-graph.md) | Every object type and its ownership/composition edges | NPS-025 §4, NPS-029 §4 |
| [Scheduler Structure](scheduler-structure.md) | Request classes, weights, and the RT reserve in the EEVDF-derived scheduler | ADR-0013, NPS-002 |
| [Memory Manager](memory-manager.md) | Per-container limits, the adopted defaults, and SUSPENDED accounting | NPS-010 §7/§9, NPS-001 §3 |
| [Game Package Layering](game-package-layering.md) | .nypkg → .nygi → mount → overlay: the full install stack | NPS-026, NPS-006, NPS-005 |
| [Update Pipeline](update-pipeline.md) | Delta verification, swap-and-retain, known-good rollback | NPS-026 §9, NPS-006 §3.2/§4.3 |
| [Package Mount Lifecycle](package-mount-lifecycle.md) | Mount → decompress → cache → unmount | NPS-006 §5 |

Still planned (Milestone 11 gap category 6): the AI subsystem and
identity subsystem diagrams. Both gates are now open — their
governing NPS documents exist (NPS-015, NPS-029; the object graph
already renders NPS-029's User/Session types) — and the update
pipeline above covers what the original "await their own NPS"
note had also listed under that gap. This file's original gating
claim predates both NPS documents existing.

## Editing Notes

- One diagram per file, with the governing specification cited, so a
  spec change that invalidates a diagram is findable.
- Prefer `flowchart`, `stateDiagram-v2`, `sequenceDiagram`, or `graph`
  — the subset supported by both GitHub and the MkDocs renderer.
