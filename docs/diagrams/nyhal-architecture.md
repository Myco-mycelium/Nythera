# NyHAL Architecture

*Source: NPS-017 §3. Each layer depends only on the layer directly beneath
it; nothing above NyCore may call into a backend's native mechanisms.*

```mermaid
graph TD
    subgraph "Applications (any runtime class)"
        NATIVE["Native applications"]
        WIN["Windows-compat (.exe/.msi) via NPS-007"]
        AND["Android-compat (.apk) via NPS-008"]
    end

    subgraph NYSDK["NySDK — developer-facing SDK, application-level APIs"]
        SDK["API-001 public surface"]
    end

    subgraph NYRUNTIME["NyRuntime"]
        RUNTIME["Compatibility runtimes · UI shell (NPS-009)<br/>Gaming (NPS-012..014) · AI (NPS-015..016)"]
    end

    subgraph NYCORE["NyCore"]
        CORE["Containers (NPS-002, NPS-010) · Capabilities (NPS-011)<br/>IPC (NPS-003) · Storage (NPS-004..006)"]
    end

    subgraph NYHAL["NyHAL — abstraction boundary"]
        HAL["Active backend"]
        LINUX["Linux Backend<br/>(Experimental — not yet conformant)"]
        NYK["NyKernel Backend<br/>(Not started)"]
        EXP["Experimental Backend<br/>(Not started)"]
    end

    HARDWARE["Hardware / host kernel"]

    NATIVE --> SDK
    WIN --> SDK
    AND --> SDK
    SDK --> RUNTIME
    RUNTIME --> CORE
    CORE --> HAL
    HAL --> LINUX
    HAL --> NYK
    HAL --> EXP
    LINUX --> HARDWARE
    NYK --> HARDWARE
    EXP --> HARDWARE
```

**Key rule** (NPS-017 §3.2): NySDK/NyRuntime code **MUST NOT** call a
backend's native mechanisms directly — all access passes through NyCore's
contracts. An application built against NySDK runs unmodified on any
conformant backend (NPS-017 §7.1).
