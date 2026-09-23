# Capability Graph

*Source: NPS-011 (the class registry), NPS-010 §4–§6 (grant, narrow,
revoke), NPS-003 §5 (transfer/attenuation, kernel sole arbiter),
ADR-0018 (the tamper-evident log). Complements the grant-flow sequence
diagram — that one shows *time*; this shows the permission
structure.*

```mermaid
flowchart LR
    subgraph Registry ["NPS-011 registry (classes, static)"]
        C1["CAP-* class<br/>(semantics, default: prompt/denied)"]
        C2["CAP-* class"]
        C3["CAP-MEDIA-* (split classes)"]
    end
    subgraph Grants ["Instances (per NPS-010 §5.1, atomic at EVALUATING)"]
        G1["grant: class → container A"]
        G2["grant: class → container B"]
    end
    K["Kernel — sole arbiter (NPS-003 §5.4)"]
    L["Audit chain (ADR-0018, hash-chained)"]
    U["User (NPS-029) — prompts flow through its Session, never grants"]

    C1 -->|grant| G1
    C2 -->|grant| G2
    G1 -->|held by| K
    G2 -->|held by| K
    G1 -->|may narrow irreversibly (§5.2)| G2
    G1 -->|every grant/narrow/revoke| L
    G2 --> L
    K -->|validates every use| L
    U -.->|no direct holding| K
```

**Rules the graph encodes:**

- Classes are static registry entries; only *instances* exist at
  runtime, minted atomically at end of EVALUATING (NPS-010 §5.1) —
  never widened after grant, narrowing is voluntary and irreversible
  (§5.2), revocation is recorded (§6).
- The kernel is the sole arbiter of validity; no user-space process
  (including the UI shell and the session manager) can forge,
  self-issue, or widen (NPS-003 §5.4, NPS-009 §7.1).
- Identity composes, not bypasses: a User's Session triggers prompts
  and starts containers, but holds nothing (NPS-029 §3.4).
- Cross-User grants (NPS-029 §5.1) are ordinary instances of this
  graph, distinguished only by both User IDs in their audit records.
