# Update Pipeline

*Source: NPS-026 §9 (delta updates — §9.1 verify-as-first-class,
§9.2 known-good rollback formalizing NPS-001 §6.3's retention),
NPS-026 §6/§7 (signature + integrity verification), NPS-006 §3.2
(new image on update), NPS-006 §4.3 (overlay persistence across
updates).*

```mermaid
sequenceDiagram
    participant PM as Package manager (user-initiated)
    participant IDX as Signed repo index (§6)
    participant V as Verification (§6, §7)
    participant FS as Image/overlay store
    participant R as Running container (NPS-010)

    PM->>IDX: check for update (index verified BEFORE any download)
    IDX-->>PM: delta available (version N → N+1)
    PM->>V: fetch delta; verify as FIRST-CLASS package (§9.1)
    alt verification fails (signature, integrity, trust)
        V-->>PM: REFUSE — no bytes touch installed content
    else verification passes
        PM->>FS: stage new .nygi image N+1 (N untouched: NPS-006 §3.2)
        PM->>R: restart/swap at operator-chosen point
        alt update succeeds
            FS->>FS: N retained as known-good (NPS-001 §6.3 retention)
        else update fails or is interrupted
            FS-->>R: previous version N intact and runnable (§9.2)
        end
    end
    Note over FS: the overlay (saves, mods) persists across BOTH paths (NPS-006 §4.3)
```

**Rules the pipeline enforces:**

- The repository index is verified before anything downloads, and a
  delta verifies **as a first-class package** (§9.1) — the same
  signature/integrity/trust gates as a full install, with no weaker
  path for "just a delta".
- A failed or interrupted update **MUST** leave the previous
  known-good version intact and runnable (§9.2) — the N image is
  retained, not deleted-then-hoped.
- The read-only image model makes swap-and-retain cheap: N+1 is a new
  image (NPS-006 §3.2), the old one is kept, and rollback is a mount
  decision, not a repair job.
- User data never rides the update: the overlay persists across
  success and rollback alike (NPS-006 §4.3).
- Open fence (recorded, not resolved): the §9.2/§9.3 canonical
  serialization decision (NPS-026 §6.7.3 / G3) gates independent
  implementations of this pipeline, not its rules.
