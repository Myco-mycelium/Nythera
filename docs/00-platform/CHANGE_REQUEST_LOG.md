# Change Request Log

Every architectural change gets an entry here, per the workflow established
during repository bootstrap. This is the engineering history of Nythera,
independent of chat or discussion history.

| ID | Description | Status | Linked ADR |
|----|--------------|--------|------------|
| CR-0001 | Adopt Diátaxis + MkDocs Material documentation structure | Accepted | ADR-0001 |
| CR-0002 | Adopt copy-on-write filesystem with built-in compression | Accepted | ADR-0002 |
| CR-0003 | Distribute games as mounted disk images with writable overlay | Accepted | ADR-0003 |
| CR-0004 | Containerize all application classes (native/Windows/Android) | Accepted | ADR-0004 |
| CR-0005 | Windows compatibility via translation layer, not full emulation | Accepted | ADR-0005 |
| CR-0006 | Adopt a hybrid microkernel as the Nythera kernel base | Accepted | ADR-0006 |
| CR-0007 | Adopt Zstandard as the default compression codec | Proposed — benchmark-blocked | ADR-0007 |
| CR-0008 | Accept NPC-001, NPC-002, NPC-003 as binding governance (Milestone 2 review) | Accepted | — |
| CR-0009 | Establish NPC-008 Subsystem Owners as the canonical ownership list | Accepted | — |
| CR-0010 | Adopt an AOSP-based container runtime for Android compatibility | Accepted | ADR-0008 |
| CR-0011 | Adopt per-container token-bucket rate limiting for IPC | Proposed — benchmark-blocked | ADR-0009 |
| CR-0012 | Adopt Vulkan as the native graphics API foundation | Accepted | ADR-0010 |
| CR-0013 | AI assistant runs as an ordinary capability-scoped container | Accepted | ADR-0011 |
| CR-0014 | Adopt NyHAL as a pluggable kernel abstraction layer (Linux/Experimental/NyKernel backends) | Accepted | ADR-0012 |
| CR-0015 | Adopt an EEVDF-derived scheduler with a real-time priority class | Proposed — tuning-blocked | ADR-0013 |
| CR-0016 | Milestone 9 Architecture Group review: accept ADR-0002/0003/0004/0005/0006/0008 and NPS-001/004/006/007/008/009/012/013/014/015/016/017; resolve VR scoping (NPS-012/009) | Accepted | — |
| CR-0017 | Adopt UEFI Secure Boot with user-enrollable keys | Proposed | ADR-0014 |
| CR-0018 | Adopt a shared dynamic binary translation approach for ARM/x86 compatibility (replaces the separate deferrals in NPS-007 §7 / NPS-008 §7) | Proposed — perf-validation-blocked | ADR-0015 |
| CR-0019 | Implement NyFS's Linux Backend as a user-space FUSE filesystem | Proposed — kernel-module fallback benchmark-gated | ADR-0016 |
| CR-0020 | Expand NPS-011 Android permission mapping with 8 capabilities (contacts, calendar, telephony, SMS, sensors, media library, NFC, biometric) | Accepted | — |
| CR-0021 | Configure and verify CI build for the MkDocs Material documentation site | Accepted | — |
| CR-0022 | Fix orphaned revision-history tables across 13 NPS documents; correct stale `repo_url` placeholder in mkdocs.yml | Accepted | — |
| CR-0023 | Add nyctr container-primitive proof-of-concept (Linux namespaces + cgroups), tested and passing 4/4 cases | Accepted | — |
| CR-0024 | Reject proposed NPS domain-grouped renumbering (NPS-100/200/...) in favor of existing sequential IDs + subsystem metadata | **Rejected** | ADR-0017 |
| CR-0025 | Establish NPC-009 Requirements Database and seed it with 29 requirements traced from existing Accepted specifications | Accepted | — |
| CR-0026 | Begin phased threat model: NPS-018 (methodology, trust boundaries) and NPS-019 (attack surface enumeration, 24 surfaces) | Accepted | — |
| CR-0027 | Threat model Phase 2: NPS-020 STRIDE analysis, all 10 trust boundaries; amend NPS-001 (GPU command validation) and NPS-003 (shared-memory zeroing) closing 2 findings; identify package-signing gap (FIND-PACKAGE-001) | Accepted | — |
| CR-0028 | Threat model Phase 3: NPS-021 privilege/escalation analysis; new ADR-0018 (hash-chained audit log); amend NPS-010 (atomic grant-check, tamper-evident audit) and NPS-011 (split CAP-MEDIA-LIBRARY); add REQ-IPC-0004; record FIND-CAPABILITY-005 against NPC-008 | Accepted | ADR-0018 |
| CR-0029 | Add tools/check_depends_on_cycles.py; fix 4 real circular dependencies it found across NPS-001, NPS-003, NPS-007, NPS-008, ADR-0012/13/14/15 (each individually reasonable when added, circular together) | Accepted | — |
| CR-0030 | Merge externally-contributed Linux Backend implementation (source/nyhal-linux-backend/), verify independently (20/20 tests pass), reconcile NPS-017 backend registry and REQ-NYHAL-0003 status accordingly | Accepted | — |
| CR-0031 | Threat model Phase 4: NPS-022 container escape analysis, grounded in the real implementation; amend NPS-017 Sec 4.1/4.2 (cgroup v1 hardening, control-plane/data-plane enforcement); add REQ-NYHAL-0004; flag implementation non-conformant against tightened requirement | Accepted | — |

## How to Add an Entry

1. Assign the next sequential `CR-XXXX` ID.
2. Write a one-line description of the change.
3. Link the ADR or NPS that contains the full rationale.
4. Set status to `Proposed`, `Accepted`, `Rejected`, or `Superseded`.
5. Update this file in the same commit as the linked document (NPC-001 §6.5).
