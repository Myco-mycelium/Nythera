# Boot Sequence

*Source: NPS-001 §5 (NyKernel Backend). Other backends satisfy the same
stages in substance without matching the stage names (NPS-017 §4.5).*

```mermaid
flowchart TD
    A["1. Firmware Handoff<br/>UEFI hands control to the Nyrqis boot loader"] --> B
    B["2. Boot Loader<br/>Verifies kernel image integrity (checksum, and signature when Secure Boot is enabled); loads kernel + minimal boot image"] --> C
    C["3. Kernel Init<br/>Initializes memory manager, interrupts, scheduler.<br/>Does NOT mount NyFS — filesystem logic is user-space"] --> D
    D["4. First Process<br/>Kernel starts a single trusted user-space 'init' with elevated initial capabilities"] --> E
    E["5. Service Bring-Up<br/>init starts, in dependency order: NyFS service, core drivers, capability/service registry; hands off to the adaptive UI shell"] --> F
    F["6. User Session<br/>Login screen or default session once required services report ready"]
```

**Failure handling** (NPS-001 §6): failure in Stage 5 **MUST NOT** halt
boot if the service isn't required for a minimal session; failure in
Stages 1–4 **MUST** halt with a diagnostic screen. Stage transitions
**MUST** be order-validated at the API level (NPS-001 §5).

**Secure Boot** (ADR-0014): the boot loader verifies against a Nyrqis
key, with user-enrollable keys for self-built or Experimental Backend
kernels.
