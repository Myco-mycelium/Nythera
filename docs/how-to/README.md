# How-To Guides

Task-oriented guides for someone who already knows the basics, per
NPC-003 §3: each one solves a specific problem in the fewest steps.

| Guide | Solves |
|-------|--------|
| [Add a New Capability to the Registry](add-a-capability.md) | The full path from "this app needs a permission" to a usable `CAP-*` entry |
| [Propose an NPS or ADR](propose-a-change.md) | The change process from first draft to accepted document, including the index files you must update in the same commit |
| [Run the Linux Backend Tests](run-linux-backend-tests.md) | Verify the only current implementation, locally, without trusting its own claims |
| [Verify the Docs Dependency Graph](check-dependency-cycles.md) | Run the cycle checker and understand what it enforces |

New how-tos **SHOULD** be written only for tasks that can actually be
performed today; a guide for a not-yet-implemented feature belongs in
[`docs/tutorials/`](../tutorials/README.md) as a spec-grounded walkthrough
instead.
