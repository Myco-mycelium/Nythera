# Architecture — Explanation

Design rationale for the core architecture subsystem. Each page here
explains *why* a decision was made; the normative specifications and
decision records live under `docs/reference/`.

| Topic | Document |
|-------|----------|
| Why a hybrid microkernel, and what it costs | [Why Nythera Uses a Hybrid Microkernel](why-hybrid-microkernel.md) |
| Why the kernel is one NyHAL backend among several | [Why NyHAL Pluggable Backends](why-nyhal-pluggable-backends.md) |

## Governing Specifications

- [NPS-001 — Kernel Architecture and Boot](../../reference/nps/NPS-001-kernel-architecture-and-boot.md) (NyKernel Backend)
- [NPS-002 — Process and Thread Model](../../reference/nps/NPS-002-process-and-thread-model.md)
- [NPS-003 — IPC and Capability Passing](../../reference/nps/NPS-003-ipc-and-capability-passing.md)
- [NPS-017 — NyHAL Kernel Abstraction](../../reference/nps/NPS-017-nyhal-kernel-abstraction.md)
- [ADR-0006 — Hybrid microkernel](../../reference/adr/ADR-0006-hybrid-microkernel.md), [ADR-0012 — NyHAL](../../reference/adr/ADR-0012-nyhal-pluggable-kernel-backend.md)
