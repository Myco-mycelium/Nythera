# Nythera Requirements Database

Governed by [`NPC-009`](../../00-platform/009-REQUIREMENTS_DATABASE.md).
This is a **seed set**, not full coverage — per NPC-009 §7.3, retroactively
extracting every MUST/SHALL from all 17 NPS documents is explicitly out of
scope for this version. What follows is a representative sample per
domain, restating existing normative language with a stable ID rather
than introducing anything new (NPC-009 §7.1).

| ID | Statement | Source | Status | Verified By | Implemented By |
|----|-----------|--------|--------|-------------|-----------------|
| REQ-BOOT-0001 | The boot loader SHALL verify kernel image integrity (checksum, and signature where Secure Boot is enabled) before loading it. | NPS-001 §5 (Stage 2) | Verified | — | — |
| REQ-BOOT-0002 | A failure in boot Stages 1–4 SHALL halt boot and present a diagnostic screen. | NPS-001 §6.2 | Verified | — | — |
| REQ-BOOT-0003 | The platform SHALL support UEFI Secure Boot with user-enrollable keys. | ADR-0014 | Verified | — | — |
| REQ-KERNEL-0001 | Every process SHALL belong to exactly one container at creation time. | NPS-002 §4.1 | Verified | — | — |
| REQ-KERNEL-0002 | A process SHALL NOT hold a capability its container does not hold. | NPS-002 §4.3 | Verified | — | — |
| REQ-IPC-0001 | All IPC message payloads SHALL be bounded in size at the primitive level. | NPS-003 §3.1 | Verified | — | — |
| REQ-IPC-0002 | The kernel SHALL be the sole arbiter of capability validity. | NPS-003 §5.4 | Verified | — | — |
| REQ-STORAGE-0001 | Every NyFS data block and metadata block SHALL be checksummed. | NPS-004 §4.3 | Verified | — | — |
| REQ-STORAGE-0002 | Creating an NyFS snapshot SHALL be an O(1) metadata operation that does not copy data eagerly. | NPS-004 §4.2 | Verified | — | — |
| REQ-COMPRESS-0001 | A user SHALL be able to view and override the compression category of any file or directory. | NPS-005 §4.4 | Verified | — | — |
| REQ-IMAGE-0001 | The read-only content layer of a `.nygi` image SHALL NOT be modifiable after image creation. | NPS-006 §3.2 | Verified | — | — |
| REQ-IMAGE-0002 | Uninstalling a `.nygi`-packaged application SHALL retain its writable overlay by default, offered for deletion as a separate explicit user choice. | NPS-006 §7.1 | Verified | — | — |
| REQ-WINCOMPAT-0001 | Each Windows-compatibility container SHALL receive its own isolated virtual registry namespace. | NPS-007 §5.1 | Verified | — | — |
| REQ-ANDROIDCOMPAT-0001 | Installing an `.apk` SHALL produce a `.nygi` image identically to any other application. | NPS-008 §4.1 | Verified | — | — |
| REQ-ANDROIDCOMPAT-0002 | An Android permission with no corresponding Nythera capability SHALL NOT be silently granted. | NPS-008 §5.2 | Verified | — | — |
| REQ-UI-0001 | A user SHALL be able to override the automatically-detected device UI mode manually. | NPS-009 §4.2 | Verified | — | — |
| REQ-SEC-0001 | A container's capability set SHALL be fixed at the end of manifest evaluation for its initial grant; capabilities SHALL NOT be added afterward except through the explicit, auditable capability-registry request path. | NPS-010 §5.1 | Verified | — | — |
| REQ-SEC-0002 | Every capability grant and revocation SHALL be recorded in a form a user can inspect. | NPS-010 §8.1 | Verified | — | — |
| REQ-CAP-0001 | Any subsystem requesting a new capability class SHALL document it in the capability registry before it may be used by any application. | NPC-001 §9.3 / NPS-011 §5 | Verified | — | — |
| REQ-INPUT-0001 | Controller input SHALL be delivered to the foreground container through the same `CAP-INPUT` capability and IPC path as keyboard/mouse/touch input. | NPS-012 §3.2 | Verified | — | — |
| REQ-GPU-0001 | An application SHALL be able to query supported GPU features (HDR, VRR, ray tracing, upscaling) at startup and degrade gracefully when a feature is unsupported. | NPS-013 §3.2 | Verified | — | — |
| REQ-EMU-0001 | The Emulator Hub SHALL NOT include built-in search, download, or acquisition functionality for ROM or BIOS files. | NPS-014 §3.3 | Verified | — | — |
| REQ-AI-0001 | Any AI assistant action that would alter system configuration, install/remove software, or change security policy SHALL be presented as a suggestion requiring the user's own separate, explicit action to execute. | NPS-015 §5.1 | Verified | — | — |
| REQ-AI-0002 | The operating system SHALL remain fully functional with the AI assistant disabled or absent. | NPS-015 §6.1 | Verified | — | — |
| REQ-SYNC-0001 | No cloud-syncable data class SHALL be synchronized without the user explicitly enabling that specific class. | NPS-016 §4.1 | Verified | — | — |
| REQ-NYHAL-0001 | A NyHAL backend SHALL be the sole arbiter of capability validity for its containers, regardless of native enforcement mechanism. | NPS-017 §4.2 | Verified | — | — |
| REQ-NYHAL-0002 | An application built against NySDK SHALL run unmodified across any conformant NyHAL backend. | NPS-017 §7.1 | Verified | — | — |
| REQ-NYHAL-0003 | A NyHAL backend SHALL provide container creation, teardown, suspension, and resource-limit enforcement sufficient to satisfy the process/container lifecycle state machines in NPS-002 §5 and NPS-010 §4. | NPS-017 §4.1 | Implemented (partial) | — | `source/nyhal-linux-backend/poc-container/nyctr.py` — creation, teardown, and a basic memory/pid resource limit only; suspension is NOT implemented, and this is namespace/cgroup isolation, not the Nythera capability model (see the PoC's own README for the full limitation list) |

## Coverage Note

29 requirements across all 17 domain prefixes defined in NPC-009 §4 — at
least one per NPS document, not a claim of exhaustive coverage of any of
them. Every `Source` cell is a real section in an already-`Accepted`
specification; none of these statements introduce a new obligation (per
NPC-009 §7.1). `REQ-NYHAL-0003` is the only entry touching `Implemented`
status, and its caveat is deliberately specific rather than a bare
checkmark, since the underlying PoC satisfies only a slice of what the
requirement as written actually asks for.

## Adding a Requirement

See NPC-009 §5–§7. In short: find the exact MUST/SHALL sentence in an
`Accepted` specification, assign the next sequence number in the relevant
domain, restate it as a single testable statement, cite the source
section, and set status to `Draft` until a second pass confirms no
meaning drift (→ `Verified`).
