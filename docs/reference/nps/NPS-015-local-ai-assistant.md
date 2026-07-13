---
title: Local AI Assistant
document_id: NPS-015
version: 1.0.1
status: Accepted
classification: Normative
subsystem: ai
owners:
  - Nythera Architecture
created: 2026-07-12
updated: 2026-07-12
ai_assisted: true
review_cycle: As needed
depends_on: [NTM-000, NPC-001, ADR-0011, NPS-010, NPS-011]
---

# NPS-015 — Local AI Assistant

## 1. Status of This Document

This document is **normative**. RFC 2119 terms apply as in NPC-001. It
implements ADR-0011 (containerized AI assistant) and directly enforces
NTM-000 §9 and NPC-001 §11 (AI boundaries) at the specification level.

## 2. Scope

This specification covers the local AI assistant's functional scope,
container placement, the suggest-vs-act boundary, offline operation
requirements, and disableability. It does not cover the AI Collaboration
Protocol (NPC-002), which governs AI used to build Nythera itself, not the
assistant shipped inside it.

## 3. Functional Scope

Per the original design discussion and NTM-000, the assistant **MAY**
provide: offline voice control, system diagnostics, automatic optimization
suggestions, file search, game performance tuning suggestions, code
assistance, and document summarization. This list **MAY** grow through the
normal change process (NPC-001 §6); any new function **MUST** be evaluated
against §5 (the suggest-vs-act boundary) before being added.

## 4. Container Placement

4.1. Per ADR-0011, the assistant **MUST** run as one or more ordinary
containers under NPS-010, with no capability beyond what is explicitly
granted in the capability registry (NPS-011).

4.2. The assistant's containers **MUST** be clearly identifiable to the
user in the audit view required by NPS-010 §8, so "what can the assistant
currently do" is answerable the same way as for any other application.

## 5. The Suggest-vs-Act Boundary

5.1. Any assistant action that would alter system configuration, install
or remove software, or change security policy **MUST** be presented to the
user as a suggestion requiring the user's own explicit action to execute,
per NPC-001 §11.1. The assistant **MUST NOT** perform the change itself
merely because the user asked it to, since "the user asked" and "the user
explicitly and separately authorized this specific change" are not treated
as equivalent by NPC-001 §11.1.

5.2. A suggestion **MUST** be specific and reviewable before execution
(e.g. "disable Bluetooth" surfaced as an actual toggle the user activates,
not a hidden action performed on the user's behalf after a conversational
"yes").

5.3. Read-only operations (diagnostics, file search, summarization) **MAY**
be performed directly by the assistant using `CAP-AI-DIAGNOSTICS-READ`
(NPS-011), since they do not alter system state and fall outside §5.1's
restriction.

5.4. Code assistance that produces files or code changes on the user's
request **MUST** write to locations the assistant's container has
explicit, ordinary filesystem capability for (per NPS-011), and **MUST
NOT** be treated as a system-configuration change under §5.1 merely
because it modifies files — §5.1 governs system/security state, not
ordinary user file content the user directed the assistant to produce.

## 6. Offline Operation

6.1. Per NTM-000 §9, the operating system **MUST** remain fully functional
with the AI assistant disabled or absent. No core OS function defined in
any other NPS document **MAY** depend on the assistant being present.

6.2. Voice control, file search, and other assistant functions **SHOULD**
be capable of operating fully offline, without requiring a network
capability, consistent with the original design discussion's "no cloud
account required for core functionality" goal.

## 7. Disableability

7.1. A user **MUST** be able to disable the AI assistant entirely through
an ordinary settings action, with the same directness as disabling any
other optional application.

7.2. Disabling the assistant **MUST NOT** require special uninstall
procedures beyond what NPS-010 §4.5–4.6 already defines for any container
teardown, since the assistant holds no capability the platform depends on
(ADR-0011).

## 8. Interaction with Performance Tuning

8.1. "Game performance tuning" suggestions (§3) **MUST** follow the
suggest-vs-act boundary in §5 identically to any other suggestion — the
assistant **MAY** recommend a specific NPS-010 resource-limit adjustment
for a container, but applying it requires the user's own action.

## 9. Open Questions *(Informative)*

- The exact UI presentation of "a reviewable, specific suggestion" (§5.2)
  is deferred to the adaptive UI shell's implementation work (NPS-009).
- Whether code assistance should have its own dedicated capability
  (distinct from ordinary filesystem access) for auditability is
  undecided and may warrant a future NPS-011 addition.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-07-12 | Initial draft |

| 1.0.1 | 2026-07-12 | Architecture Group review completed (Milestone 9). Status: Draft → Accepted. |

---
**End of Document**
