# Game Package Layering

*Source: NPS-026 (.nypkg installable unit — manifest, signature block,
verification pipeline), NPS-006 (.nygi image structure §3, overlay §4,
mount lifecycle §5), NPS-005 §5 (compression), NPS-011 (the
capability gates at each boundary).*

```mermaid
flowchart TD
    subgraph Publisher
        SRC["App/game content"]
        PKG[".nypkg<br/>manifest (§5.1) + content layers + signature block (§6)"]
        SRC --> PKG
    end
    subgraph Installer ["Installer (user-initiated, NPS-025 §4.4)"]
        V["verify signature (§6, §7) — TOFU fail-closed"]
        EVAL["evaluate manifest → capability flow (NPS-010 §4.2)"]
        IMG["write .nygi image (read-only)"]
        PKG --> V --> EVAL --> IMG
    end
    subgraph Storage ["NyFS / ADR-0002 CoW"]
        BASE["READ-ONLY content layer (NPS-006 §3.2 — immutable after creation)"]
        OVER["writable overlay (NPS-006 §4.1) — survives uninstall by default (§7)"]
    end
    subgraph Runtime ["Launch (NPS-006 §5)"]
        MNT["mount: base + overlay"]
        APPC["Application container (NPS-010 §4)"]
        MODS["Mods — written into the overlay (§4.2)"]
    end

    IMG --> BASE
    MNT --> BASE
    MNT --> OVER
    APPC -->|sees merged view| MNT
    MODS --> OVER
    EVAL -.->|CAP-* gates| APPC
```

**Rules the layering encodes:**

- The publisher's signature covers the manifest and content (NPS-026
  §6); verification is a precondition of install, never a warning
  (the FIND-PACKAGE-001/003 line, closed by NPS-027/NPS-026 §6).
- The `.nygi` content layer is **immutable** after creation (NPS-006
  §3.2); every mutation — saves, mods, settings — lands in the
  per-image overlay, which outlives uninstalls by default (§7).
- Capability requests flow only through the manifest (EVAL), evaluated
  by the ordinary container path; a package never grants itself
  anything (NPS-026 §2's scope fence, NPS-010 §4.2).
- One diagram boundary is deliberately absent: the delta-update path
  (NPS-026 §9) — it re-enters this stack through the Installer's
  verification gate and is drawn in the update-pipeline diagram.
