# Object Graph

*Source: NPS-025 §4 (the object type catalogue) — this diagram renders
the catalogue's Relationships rows; the types' normative definitions
live in NPS-025 (User/Session in NPS-029 §4).*

```mermaid
flowchart TD
    U[User — NPS-029] -->|owns| WS[Workspace]
    U -->|owns| PKG[Package]
    U -->|owns| DATA[Per-user data volume — NPS-004]
    U -->|has| SESS[Session — NPS-029]
    SESS -->|stamps containers| APP[Application]
    WS -->|owns| WIN[Window]
    APP -->|hosts| WIN
    APP -->|instance of| PKG
    APP -->|holds| CAP[Capability instance]
    PKG -->|produces| GAME[Game]
    PKG -->|declares deps| PKG
    GAME -->|hosts| MOD[Mod]
    APP -->|may launch| GAME
    CTRL[Controller] -.->|backed by| DEV[Device]
    GPU[GPU] -.->|backed by| DEV
    APP -->|consumes| GPU
    APP -->|posts| NOTIF[Notification]
    APP -->|hosts| AI[AI Conversation]
    SVC[Service] -->|composes runtime| APP
    DEV -.-> CAP
    CAP -.->|gates every edge| APP
```

**Rules the graph encodes:**

- Every solid edge is ownership or composition (NPS-025 §4's
  Relationships rows); dashed edges are the capability-gated access
  paths (§3.2 — no edge exists outside the capability model).
- `User` owns objects but holds **no** capabilities directly — authority
  flows only through Sessions into containers (NPS-029 §3.1.3).
- Object IDs are stable and never reused across the whole graph
  (NPS-025 §3.1).
