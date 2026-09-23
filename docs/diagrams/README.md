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
| [Package Mount Lifecycle](package-mount-lifecycle.md) | Mount → decompress → cache → unmount | NPS-006 §5 |

Still planned (Milestone 11 gap category 6): memory manager, game
package layering, AI subsystem, and update pipeline. The identity
diagram's gate is resolved — its governing NPS now exists
(`NPS-029`), and the object graph above already renders its
User/Session types; a dedicated identity-subsystem diagram is now
draftable at any time. The AI diagram's older note (awaiting "its
own NPS") predates NPS-015 (Local AI Assistant) and should be
re-audited against NPS-015 before drawing — this file's original
gating claim was written before either existed.

## Editing Notes

- One diagram per file, with the governing specification cited, so a
  spec change that invalidates a diagram is findable.
- Prefer `flowchart`, `stateDiagram-v2`, `sequenceDiagram`, or `graph`
  — the subset supported by both GitHub and the MkDocs renderer.
