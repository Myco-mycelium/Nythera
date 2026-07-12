# Change Request Log

Every architectural change gets an entry here, per the workflow established
during repository bootstrap. This is the engineering history of Nythera,
independent of chat or discussion history.

| ID | Description | Status | Linked ADR |
|----|--------------|--------|------------|
| CR-0001 | Adopt Diátaxis + MkDocs Material documentation structure | Accepted | ADR-0001 |
| CR-0002 | Adopt copy-on-write filesystem with built-in compression | Proposed | ADR-0002 |
| CR-0003 | Distribute games as mounted disk images with writable overlay | Proposed | ADR-0003 |
| CR-0004 | Containerize all application classes (native/Windows/Android) | Proposed | ADR-0004 |
| CR-0005 | Windows compatibility via translation layer, not full emulation | Proposed | ADR-0005 |
| CR-0006 | Adopt a hybrid microkernel as the Nythera kernel base | Proposed | ADR-0006 |
| CR-0007 | Adopt Zstandard as the default compression codec | Proposed | ADR-0007 |
| CR-0008 | Accept NPC-001, NPC-002, NPC-003 as binding governance (Milestone 2 review) | Accepted | — |
| CR-0009 | Establish NPC-008 Subsystem Owners as the canonical ownership list | Accepted | — |
| CR-0010 | Adopt an AOSP-based container runtime for Android compatibility | Proposed | ADR-0008 |

## How to Add an Entry

1. Assign the next sequential `CR-XXXX` ID.
2. Write a one-line description of the change.
3. Link the ADR or NPS that contains the full rationale.
4. Set status to `Proposed`, `Accepted`, `Rejected`, or `Superseded`.
5. Update this file in the same commit as the linked document (NPC-001 §6.5).
