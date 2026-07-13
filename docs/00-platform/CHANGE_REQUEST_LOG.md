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

## How to Add an Entry

1. Assign the next sequential `CR-XXXX` ID.
2. Write a one-line description of the change.
3. Link the ADR or NPS that contains the full rationale.
4. Set status to `Proposed`, `Accepted`, `Rejected`, or `Superseded`.
5. Update this file in the same commit as the linked document (NPC-001 §6.5).
