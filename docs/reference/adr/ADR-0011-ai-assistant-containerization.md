---
title: AI Assistant Runs as an Ordinary Capability-Scoped Container
document_id: ADR-0011
version: 1.0.1
status: Accepted
owners: [Nythera Architecture]
created: 2026-07-12
updated: 2026-07-13
ai_assisted: true
depends_on: [NTM-000, NPC-001, ADR-0004, ADR-0006, NPS-010, NPS-011]
---

# ADR-0011 — AI Assistant as an Ordinary Capability-Scoped Container

## Context
NTM-000 §9 states AI "is a tool. It is not the operating system," and must
remain fully removable without loss of core functionality. NPC-001 §11
already forbids the AI from altering configuration, installing software,
or changing security policy without an explicit, auditable user action.
Neither document specifies *how* the AI assistant is architecturally
placed — whether it runs with special system access to do its job (system
diagnostics, performance tuning) or through the same container model as
everything else.

## Decision (Proposed)
The local AI assistant **MUST** run as one or more ordinary containers
under the container runtime (NPS-010), holding only capabilities defined
in the capability registry (NPS-011) — the same model already applied
uniformly to native apps, Windows/Android compatibility runtimes
(NPS-007, NPS-008), and the Emulator Hub (NPS-014). "Being the system
assistant" is not itself a capability and grants no implicit elevated
access.

Where the assistant needs to observe system state for diagnostics or
performance tuning, it does so through new, narrowly-scoped, read-only
capabilities (defined in NPS-011 §3 updates) rather than a general
system-access grant. Where it needs to *change* something, it does so by
producing a suggested action that a human executes through the normal,
already-capable path (e.g. the assistant suggests a setting change; the
user's own action, not the assistant's, performs it) — directly
implementing NPC-001 §11.1.

## Alternatives Considered
- **Grant the AI assistant a broad "system" capability** — rejected;
  directly conflicts with NPC-001 §11.1 and would make the assistant a
  uniquely privileged process type, undermining the uniform container
  model established since ADR-0004 and reaffirmed in every milestone
  since (NPS-007 §3.2, NPS-008 §3, NPS-012 §3.2, NPS-014 §5.1).
- **Run the AI assistant in kernel space for performance** — rejected
  outright; kernel space is limited to the explicit list in NPS-001 §3 and
  an AI assistant is definitionally not on it, per ADR-0006.
- **Let the AI assistant execute suggested actions directly, with logging
  for audit** — rejected; logging after the fact is not the same as an
  "explicit, auditable user action" required by NPC-001 §11.1 — the
  action must originate from the human, not merely be attributable to one
  after execution.

## Consequences
- NPS-011 requires new capability entries for AI-specific read access
  (system diagnostics, performance counters) distinct from existing
  capabilities, since none of the current 14 entries cover this.
- NPS-015 (Local AI Assistant) must define the suggest-vs-act boundary
  precisely enough that "suggest a change" cannot be trivially
  reclassified as "make a change with extra steps."
- Disabling the AI assistant (NTM-000 §9) reduces to simply not launching
  its containers — no special uninstall or reconfiguration path is needed,
  since it never held any capability the rest of the system depends on.

## Status
Accepted — 2026-07-12, following Architecture Group review (Milestone 9). NPS-015 (Local AI Assistant) and the NPS-011 capability additions implement this decision.
