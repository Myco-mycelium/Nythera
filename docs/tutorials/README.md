# Tutorials

Learning-oriented guides that teach by doing, per NPC-003 §3: they assume
no prior knowledge and take you through a complete, working path.

Nythera is a specification-first project — most of the platform has not
been implemented yet, so the tutorials here are of two kinds:

1. **Works-today tutorials** — things you can actually do in this
   repository right now (tour the repo, read the specs, run the Linux
   Backend's tests).
2. **Spec-grounded tutorials** — walkthroughs of a contract the
   specifications define (e.g. a container manifest), written against the
   normative text so they remain correct once implementation lands.
   These are explicitly labeled where the exact field names are deferred
   to a later specification.

| Tutorial | Kind | Prerequisites |
|----------|------|---------------|
| [A Tour of the Nythera Repository](first-repository-tour.md) | Works today | None |
| [Authoring Your First Container Manifest](authoring-your-first-manifest.md) | Spec-grounded | [A Tour of the Nythera Repository](first-repository-tour.md) |

New tutorials **SHOULD** follow the same shape: a stated outcome, a
complete worked example, and a "what just happened" recap tying the steps
back to the governing specification.
