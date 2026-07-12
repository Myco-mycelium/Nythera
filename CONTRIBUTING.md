# Contributing to Nythera

Nythera is governed by a small set of foundational documents. Read them
before contributing:

1. [NTM-000 The Nythera Manifest](docs/00-platform/000-THE_NYTHERA_MANIFEST.md)
2. [NPC-001 Project Constitution](docs/00-platform/001-PROJECT_CONSTITUTION.md)
3. [NPC-002 AI Collaboration Protocol](docs/00-platform/002-AI_COLLABORATION_PROTOCOL.md) (if using AI assistance)
4. [NPC-003 Engineering Handbook](docs/00-platform/003-ENGINEERING_HANDBOOK.md)

## Quick Workflow

1. Check [`REPOSITORY_STATE.md`](docs/00-platform/REPOSITORY_STATE.md) to see
   what currently exists and what milestone is active.
2. For a new idea or change, open an ADR (`docs/reference/adr/`) or NPS
   (`docs/reference/nps/`) draft describing the problem, proposed change,
   and alternatives considered — see NPC-001 §6.
3. Tag the relevant subsystem owner(s) for review.
4. Once accepted, update in the **same commit**:
   - [`004-SPECIFICATION_INDEX.md`](docs/00-platform/004-SPECIFICATION_INDEX.md)
   - [`005-ADR_INDEX.md`](docs/00-platform/005-ADR_INDEX.md) (if applicable)
   - [`REPOSITORY_STATE.md`](docs/00-platform/REPOSITORY_STATE.md)
   - [`CHANGE_REQUEST_LOG.md`](docs/00-platform/CHANGE_REQUEST_LOG.md)

## Commit Style

Use [Conventional Commits](https://www.conventionalcommits.org/):
`docs:`, `feat:`, `fix:`, `refactor:`, `test:`, `chore:`.

Branch names should reference the document or milestone, e.g.
`npc-004/repository-standards`.

## Document Standards

Every normative document needs YAML front-matter with at minimum: `title`,
`document_id`, `version`, `status`, `owners`, `created`, `updated`,
`depends_on`. See any file in `docs/00-platform/` for the exact schema.

## AI-Assisted Contributions

If a document or code file was substantially drafted with AI assistance, add
`ai_assisted: true` to its front-matter and, ideally, an `Assisted-by:`
trailer in the commit message. See NPC-002 for full rules — AI-assisted work
follows the same review bar as any other contribution and requires a named
human owner.
