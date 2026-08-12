# AI — Explanation

Design rationale for the AI subsystem (local assistant boundaries,
optional cloud synchronization).

| Topic | Document |
|-------|----------|
| Why the assistant suggests but never acts on its own | [Why the Assistant Suggests but Doesn't Act](why-suggest-not-act.md) |

## Governing Specifications

- [NPS-015 — Local AI Assistant](../../reference/nps/NPS-015-local-ai-assistant.md)
- [NPS-016 — Optional Cloud Synchronization](../../reference/nps/NPS-016-optional-cloud-synchronization.md)
- [ADR-0011 — AI assistant containerization](../../reference/adr/ADR-0011-ai-assistant-containerization.md)

Note: this page explains AI *inside the shipped operating system* (NTM-000
§9). AI used to *build* Nythera itself is governed by
[NPC-002 — AI Collaboration Protocol](../../00-platform/002-AI_COLLABORATION_PROTOCOL.md).
