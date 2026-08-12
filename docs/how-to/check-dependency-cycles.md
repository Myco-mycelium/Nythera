# How to Verify the Docs Dependency Graph

*Applies to: anyone adding or editing a normative document with a
`depends_on` front-matter field.*

## When you need this

Before and after any change to a normative document — especially when
adding a new one that depends on existing documents. Four real circular
dependencies were caught by this tool after they'd already been committed
(see `tools/check_depends_on_cycles.py`'s docstring for the history); the
point of running it is that nothing like that sits in `main` unnoticed.

## Steps

### 1. Run the checker

From the repository root:

```bash
python3 tools/check_depends_on_cycles.py
```

Expect `No cycles found across N documents.` (N grows as documents are
added.) It scans every non-README markdown file under `docs/00-platform/`
and `docs/reference/`, reads the `document_id` and `depends_on` fields
from front-matter, and verifies the graph is a DAG.

### 2. If it finds a cycle, fix the back-reference

The convention (from the tool's docstring): a document **MAY** cite
another document that depends on it *in prose* ("per NPS-001 §..."), but
**MUST NOT** list it in its own `depends_on` front-matter if that closes a
loop. When in doubt, the earlier/more foundational document should not
depend on the later one that analyzes or amends it. Remove the offending
back-reference, not the prose citation.

### 3. Run it again

The tool exits 1 on cycles, so it can be (and should be) wired into CI as
a pre-merge check — it's currently a manual step (see
`REPOSITORY_STATE.md` Next Actions #17).

## Checklist

- [ ] `python3 tools/check_depends_on_cycles.py` passes before committing
- [ ] If a cycle appeared, only the back-reference was removed — the
      prose citation stays
- [ ] `mkdocs build --strict` still passes for the affected doc
