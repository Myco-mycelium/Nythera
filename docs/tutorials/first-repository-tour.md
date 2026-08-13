# A Tour of the Nyrqis Repository

*Tutorial — works today. You don't need anything installed except `git`
and a text editor.*

## What you'll end up with

A mental map of the repository: where the rules live, where the
specifications live, where the code lives, and how they're wired together.
This is the map you'll want before reading anything else in the project.

## Step 1 — Clone the repository

```bash
git clone https://github.com/Myco-mycelium/Nythera.git
cd Nyrqis
```

## Step 2 — Read the Manifest first

The file `docs/00-platform/000-THE_NYRQIS_MANIFEST.md` (NTM-000) is the
philosophy document. It's short, it doesn't change, and every other
document in the project is supposed to be consistent with it. Read §4
(Principles) and §5 (What Nyrqis Will Never Become) — you'll see phrases
from those sections quoted all over the rest of the docs.

## Step 3 — Understand the layout

From the repository root:

```
docs/            # All documentation, organized by Diátaxis category
  ├── 00-platform/   # Governance: Manifest, Constitution, handbooks, indices
  ├── tutorials/     # (you are here) — learn by doing
  ├── how-to/        # task guides
  ├── reference/     # precise technical specs: ADRs, NPS, ABI, API
  ├── explanation/   # design rationale: "why"
  └── diagrams/      # Mermaid source diagrams
source/          # implementation code (the Linux Backend lives here)
tools/           # developer tooling (e.g. the docs cycle checker)
tests/           # benchmark plans, future conformance suites
sdk/             # future developer SDK (empty for now)
examples/        # future example applications (empty for now)
engineering/     # working notes, RFC drafts (empty for now)
```

The layout itself is normative — `docs/00-platform/003-ENGINEERING_HANDBOOK.md`
(NPC-003 §2) defines it, and top-level directories **MUST NOT** be renamed
without an ADR.

## Step 4 — Read the document index

Open `docs/00-platform/004-SPECIFICATION_INDEX.md` (NPC-004). This is the
master index of every canonical document in the project: the Manifest
(NTM-000), the governance documents (NPC-001..009), every Architecture
Decision Record (ADR-0001..0020), and every specification
(NPS-001..027). If you ever wonder "is there a document about X?", this
is the first place to look.

Two other indices are worth knowing:

- `docs/00-platform/005-ADR_INDEX.md` (NPC-005) — ADR statuses at a glance.
- `docs/00-platform/REPOSITORY_STATE.md` — a living snapshot of what
  exists and what's next. Read this before starting any session of work;
  it's the project's canonical "where we are" answer.

## Step 5 — Read one specification end to end

Specifications use a fixed skeleton (front-matter, normative sections,
revision history). A good first one is
[`docs/reference/nps/NPS-006-game-image-format.md`](../reference/nps/NPS-006-game-image-format.md)
(NPS-006): it's short, it's `Accepted`, and it shows the RFC 2119
language pattern ("**MUST**", "**SHOULD**") used everywhere.

Notice the YAML front-matter block at the top — `document_id`, `version`,
`status`, `depends_on`. That block is *machine-checked*: the project has a
tool that verifies `depends_on` never forms a cycle.

## Step 6 — Meet the code

The only implementation so far is the Linux Backend at
`source/nyhal-linux-backend/`. Read its status document first —
`IMPLEMENTATION_STATUS.md` — then skim `backend/container.py`. Pay
attention to how honestly the status document describes what is and isn't
done; that discipline is a project value, not an accident.

If you want to see it run (needs Python 3.12+):

```bash
cd source/nyhal-linux-backend
python3 -m pip install -r requirements.txt
python3 -B test_backend.py                 # expect 150/150 passing

(The suite is unittest-based; the exact test count is recorded in
`IMPLEMENTATION_STATUS.md`.)
```

## Step 7 — Check the tooling

`tools/check_depends_on_cycles.py` verifies the documentation's dependency
graph is a DAG:

```bash
python3 tools/check_depends_on_cycles.py   # expect "No cycles found"
```

## What just happened

You now know the four things every Nyrqis contributor needs to find fast:

1. **The philosophy** — `docs/00-platform/000-THE_NYRQIS_MANIFEST.md`
2. **The rules** — `docs/00-platform/001-PROJECT_CONSTITUTION.md` and the
   rest of `00-platform/`
3. **The specs** — `docs/00-platform/004-SPECIFICATION_INDEX.md` is the
   map; `docs/reference/` is the territory
4. **The current state** — `docs/00-platform/REPOSITORY_STATE.md`

Next, try the
[Authoring Your First Container Manifest](authoring-your-first-manifest.md)
tutorial to apply the document-reading skills you just built.
