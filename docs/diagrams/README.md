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
| [Package Mount Lifecycle](package-mount-lifecycle.md) | Mount → decompress → cache → unmount | NPS-006 §5 |

Still planned (Milestone 11 gap category 6): object graph, capability
graph, scheduler, memory manager, game package layering, AI subsystem,
identity subsystem, and update pipeline — the last two await their own
NPS documents before being diagrammed (per the roadmap's rule).

## Editing Notes

- One diagram per file, with the governing specification cited, so a
  spec change that invalidates a diagram is findable.
- Prefer `flowchart`, `stateDiagram-v2`, `sequenceDiagram`, or `graph`
  — the subset supported by both GitHub and the MkDocs renderer.
