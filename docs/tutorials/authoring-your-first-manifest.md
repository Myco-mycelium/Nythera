# Authoring Your First Container Manifest

*Tutorial — spec-grounded. There is no manifest tooling yet; this
tutorial teaches the *contract* the specifications define, so the
manifest you learn to write here is the one implementation will
eventually accept.*

## What you'll end up with

A container manifest for a small example application, written against
NPS-010 §4 and NPS-011, and a working understanding of why every field in
it exists.

## Background: what a manifest is

Every Nyrqis application runs inside a **container** (ADR-0004) — an
isolated execution boundary with an explicit, user-visible permission set
(NPC-001 §9.1). A **container manifest** is the declarative description of
what a container asks for: which capabilities, and which resource limits
(NPS-010 §3).

The manifest doesn't grant anything by itself. It's a *request* that gets
evaluated (NPS-010 §4.2), and the evaluation has teeth:

- A manifest requesting an **undefined capability** **MUST** be rejected
  (NPC-001 §9.3).
- A capability the requester can't itself grant **MUST** be rejected
  (NPS-002 §7.1).
- The validity check and the grant **MUST** be one atomic operation, so a
  capability can't be deprecated between check and grant (NPS-010 §4.2).

## Step 1 — Start from the capability registry

The only capability names you're allowed to put in a manifest are the ones
registered in [`NPS-011`](../reference/capability-registry/NPS-011-capability-registry.md).
Open it. Find a few entries and notice their columns: each capability has
a **Risk Tier** and a **Default Grant** behavior.

For our example — a simple notes application — we need:

- `CAP-DISPLAY` — present a window (Risk: Low, *Default grant*)
- `CAP-INPUT` — receive input events (Risk: Low, *Default grant*)
- `CAP-NOTIFY` — post notifications (Risk: Low, *Default grant*)
- `CAP-DOCUMENTS` — read/write the user's documents (Risk: Medium,
  *Prompt required*)

Everything we want exists in the registry, so the manifest is valid from a
capability standpoint. (If we'd needed something not registered — say,
access to a sensor that has no entry — the manifest **MUST** be rejected,
and the right fix is to add the capability to the registry first, per
[NPC-001 §9.3 and NPS-011 §5](../how-to/add-a-capability.md).)

## Step 2 — Write the manifest

A manifest is structured data. The exact serialization and field names
are deferred to the package-format specification (NPS-006 §9); what's
normative today is the *content*: the requested capability set and the
resource limits (NPS-010 §4.2, §7). The example below uses a YAML-style
layout with field names that follow the specification's vocabulary (the
`cpu_limit` / `memory_limit` values are *illustrative examples*, not
platform defaults — NPS-010 §9 defers default resource values to
benchmarking):

```yaml
manifest_version: 1
application:
  id: com.example.notes
  name: Notes
  runtime: native            # native | windows-compat | android-compat (NPS-007/008)
capabilities:
  - CAP-DISPLAY              # Low, default grant
  - CAP-INPUT                # Low, default grant
  - CAP-NOTIFY               # Low, default grant
  - CAP-DOCUMENTS            # Medium — will prompt the user at install
resources:
  ipc_rate_limit: default    # MUST be assigned at creation (NPS-010 §7.1)
  cpu_limit: 25%             # SHOULD be assignable (NPS-010 §7.2)
  memory_limit: 512 MiB      # SHOULD be assignable (NPS-010 §7.2)
```

## Step 3 — Trace what happens to it

Following NPS-010 §4, the manifest goes through a state machine:

1. **REQUESTED** — submitted by the package installer.
2. **EVALUATING** — the capability set is checked against the registry
   and against what the requester may grant; the user is prompted for the
   `Prompt required` capabilities (`CAP-DOCUMENTS` in our example).
3. **ACTIVE** — the container starts with exactly the granted set.
   Nothing can be added later except through the auditable
   capability-registry request path (NPS-010 §5.1) — never silently.
4. **SUSPENDED / TERMINATING / TERMINATED** — later states you'll see if
   the app is backgrounded or uninstalled.

Two properties worth internalizing:

- **Narrowing is irreversible.** The container *may* voluntarily drop a
  capability, but can never re-grant it to itself (NPS-010 §5.2).
- **The prompt is a grant, not a formality.** `Prompt required` must
  result in a user-visible prompt before the grant completes, unless the
  user previously granted that exact capability to that exact application
  and hasn't revoked it (NPS-011 §4.2).

## Step 4 — Check the audit view

Once the container is ACTIVE, every grant and revocation lands in the
hash-chained audit log (NPS-010 §8.1, ADR-0018) — the same mechanism used
for AI-suggestion logging (NPS-015 §5.5). A user should be able to answer
"what can Notes currently do?" by reading that log. When you author a
manifest, ask yourself whether each capability you request is one you'd
be comfortable having permanently visible in that view; if not, it
probably shouldn't be in the manifest.

## What just happened

You wrote a manifest, validated it against the capability registry,
traced it through the container lifecycle, and connected it to the audit
and prompting behavior the platform guarantees. You now understand the
single most important security idea in Nyrqis: **applications are not
granted trust; they are granted capabilities, one registry entry at a
time** (NPS-011 §1).

## Going further

- [How to add a new capability to the registry](../how-to/add-a-capability.md)
- [NPS-010 — Container Runtime](../reference/nps/NPS-010-container-runtime.md), the full normative text
- [NPS-011 — Capability Registry](../reference/capability-registry/NPS-011-capability-registry.md), the registry itself
