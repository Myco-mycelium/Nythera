# AI Subsystem

*Source: NPS-015 (§4 container placement, §5 the suggest-vs-act
boundary, §6 offline, §7 disableability), NPS-024 (FIND-AI-001..004 —
the findings that shaped §5), NPS-025 §4.11 (the AI Conversation
object), ADR-0018 (the suggestion log's mechanism).*

```mermaid
sequenceDiagram
    participant U as User
    participant AC as Assistant container (ordinary, ADR-0011)
    participant FS as Filesystem (ordinary CAP-* gates)
    participant PS as Protected system-UI surface (unspoofable, §5.2)
    participant SYS as System configuration
    participant L as Suggestion log (ADR-0018, hash-chained)

    U->>AC: request (diagnose / search / write code / change a setting)

    alt read-only diagnostics
        AC->>AC: CAP-AI-DIAGNOSTICS-READ only (§5.3) — direct, no prompt
    else file search or code assistance
        AC->>FS: ordinary per-application filesystem capability (FIND-AI-003)
        Note over AC,FS: never under the diagnostics capability; autostart/<br/>persistence locations are EXCLUDED (FIND-AI-004) — those<br/>re-enter the suggestion path below
    else system / security configuration change
        AC->>PS: specific, reviewable suggestion (§5.1) — NEVER executed by the assistant
        PS->>U: the real toggle/action (cannot be drawn over or faked, FIND-AI-001)
        U->>SYS: user's OWN explicit action executes the change
        AC->>L: suggestion + outcome (approved / declined / ignored, §5.5 — FIND-AI-002)
    end
```

**Rules the flow encodes:**

- "The user asked" and "the user explicitly and separately authorized
  this specific change" are **not** equivalent (NPS-015 §5.1,
  NPC-001 §11.1) — the assistant never performs a §5.1 change itself.
- The confirmation is only trustworthy because of the **protected
  surface**: what the user reviews is guaranteed to be the assistant's
  real suggestion, not a fake rendered by another container (§5.2,
  FIND-AI-001).
- There is no AI-only exception anywhere: file search and code
  assistance use the same ordinary filesystem capabilities as any
  application (§5.3/§5.4), and writing to autostart/persistence
  locations is a system change by another route (§5.4, FIND-AI-004).
- The suggestion record outlives the conversation: same ADR-0018
  hash-chained mechanism as capability audit events (§5.5).
- The whole subsystem is removable: the OS is fully functional with it
  disabled or absent (§6.1), disabling is an ordinary settings action
  (§7.1), and offline operation needs no network capability (§6.2).
