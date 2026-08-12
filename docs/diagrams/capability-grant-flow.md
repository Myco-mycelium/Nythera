# Capability Grant Flow

*Source: NPS-010 §4.2 (atomic check-and-grant), NPS-011 §4 (default
grant behavior), NPS-003 §5 (transfer/attenuation).*

```mermaid
sequenceDiagram
    participant P as Package installer
    participant R as Container runtime (EVALUATING)
    participant REG as Capability registry (NPS-011)
    participant U as User
    participant K as Kernel (sole arbiter)
    participant L as Audit log (ADR-0018)

    P->>R: submit manifest (requested CAP-* set)
    R->>REG: check capability set (single consistent read)
    REG-->>R: valid / invalid
    alt undefined or untraceable capability
        R-->>P: REJECT (NPC-001 §9.3)
    else all capabilities valid
        loop each "Prompt required" capability
            R->>U: user-visible prompt
            U-->>R: grant / deny
        end
        R->>K: atomically check-and-grant
        K->>L: record grant (hash-chained)
        K-->>R: ACTIVE
        R-->>P: container started
    end
```

**Rules the flow enforces:**

- A `Prompt required` capability must produce a user-visible prompt
  before the grant completes, unless previously granted to this exact
  application and not revoked (NPS-011 §4.2).
- A `Denied by default` capability is not grantable through the standard
  prompt flow at all (NPS-011 §4.3).
- The kernel is the **sole arbiter** of capability validity — no
  user-space process can forge or self-issue (NPS-003 §5.4).
- Narrowing is allowed at transfer time; widening is not (NPS-003 §5.3).
