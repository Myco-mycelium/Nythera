# Assets

Binary and static assets used by the documentation site (logos, screenshots,
favicons, sample images) live here.

## Policy

- Assets **SHOULD** be referenced from the docs tree with relative paths so
  the MkDocs build and the GitHub repository stay consistent (NPC-003 §4.4).
- Diagrams **SHOULD NOT** be stored here as binary images — per NPC-003
  §4.3, diagrams are stored as source (Mermaid or SVG) in the
  [`docs/diagrams/`](../diagrams/README.md) directory, and this directory
  is for non-diagram assets only.
- Large files and anything that changes frequently with the codebase
  (screenshots of the UI shell, for example) are not appropriate here while
  the platform has no implementation to screenshot; expect this directory to
  stay mostly empty until then.

## Current Contents

None yet.
