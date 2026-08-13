# Architecture Decision Records

An Architecture Decision Record (ADR) captures a specific decision, the
alternatives considered, and the reasoning — see NPC-001 §6 and NPC-005 for
the governing process and status semantics. Full records live in this
directory; this index tracks status only.

| ID | Title | Status |
|----|-------|--------|
| [ADR-0001](ADR-0001-diataxis-mkdocs.md) | Adopt Diátaxis + MkDocs Material for documentation | Accepted |
| [ADR-0002](ADR-0002-filesystem.md) | Adopt copy-on-write filesystem with built-in compression | Accepted |
| [ADR-0003](ADR-0003-game-disk-images.md) | Games distributed as mounted disk images with writable overlay | Accepted |
| [ADR-0004](ADR-0004-containerized-execution.md) | Containerized execution model for all application classes | Accepted |
| [ADR-0005](ADR-0005-windows-compat-layer.md) | Windows compatibility via translation layer, not full emulation | Accepted |
| [ADR-0006](ADR-0006-hybrid-microkernel.md) | Adopt a hybrid microkernel as the Nyrqis kernel base | Accepted |
| [ADR-0007](ADR-0007-compression-codec.md) | Adopt Zstandard as the default compression codec | Proposed — benchmark-blocked |
| [ADR-0008](ADR-0008-android-runtime-approach.md) | Adopt an AOSP-based container runtime for Android compatibility | Accepted |
| [ADR-0009](ADR-0009-ipc-rate-limiting.md) | Per-container token-bucket rate limiting for IPC | Proposed — benchmark-blocked |
| [ADR-0010](ADR-0010-vulkan-graphics-foundation.md) | Adopt Vulkan as the native graphics API foundation | Accepted |
| [ADR-0011](ADR-0011-ai-assistant-containerization.md) | AI assistant runs as an ordinary capability-scoped container | Accepted |
| [ADR-0012](ADR-0012-nyhal-pluggable-kernel-backend.md) | Adopt NyHAL as a pluggable kernel abstraction layer | Accepted |
| [ADR-0013](ADR-0013-scheduler-algorithm.md) | Adopt an EEVDF-derived scheduler with a real-time priority class | Proposed — tuning-blocked |
| [ADR-0014](ADR-0014-secure-boot-key-management.md) | Adopt UEFI Secure Boot with user-enrollable keys | Proposed |
| [ADR-0015](ADR-0015-shared-arm-translation.md) | Shared dynamic binary translation approach for ARM/x86 compatibility | Proposed |
| [ADR-0016](ADR-0016-nyfs-linux-backend-fuse.md) | NyFS Linux Backend implemented as a user-space FUSE filesystem | Proposed |
| [ADR-0017](ADR-0017-reject-nps-renumbering.md) | Reject domain-grouped NPS renumbering | **Rejected** |
| [ADR-0018](ADR-0018-hash-chained-audit-log.md) | Hash-chained append-only log for capability audit records | Proposed |
| [ADR-0019](ADR-0019-journal-commit-default.md) | Journal commit as the default NyFS save() mode | Proposed |
| [ADR-0020](ADR-0020-implementation-languages.md) | Implementation languages and the platform boundary | Proposed |

## Blocked Statuses

Four ADRs are held at `Proposed` pending benchmark data rather than open
architecture questions — their algorithm/approach decisions are made; only
the tuning parameters or validation numbers are missing. See
the benchmark plan (`tests/BENCHMARK_PLAN.md`) for the
methodology of every pending benchmark and
[`REPOSITORY_STATE.md`](../../00-platform/REPOSITORY_STATE.md) for the
consolidated next-actions list.
