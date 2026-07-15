---
title: STRIDE Analysis per Trust Boundary
document_id: NPS-020
version: 1.0.0
status: Draft
classification: Normative
subsystem: security
owners:
  - Nythera Architecture
created: 2026-07-13
updated: 2026-07-13
ai_assisted: true
review_cycle: As needed
depends_on: [NTM-000, NPC-001, NPC-009, NPS-018, NPS-019]
---

# NPS-020 — STRIDE Analysis per Trust Boundary

## 1. Status of This Document

This document is **normative** as a findings record: every finding
**MUST** use the format defined in NPS-018 §3, and every finding scoring
`High` on either axis of NPS-018 §6 **MUST** have a tracked follow-up
(§9 records where each one went). This is **Phase 2** of the threat
model, applying the NPS-018 framework to every surface catalogued in
NPS-019 §3.

## 2. Scope

This document analyzes all 10 trust boundaries from NPS-018 §4 and all 24
surfaces from NPS-019 §3. Not every STRIDE category is meaningfully
distinct for every surface; only categories that produce a genuine,
non-redundant finding are recorded. Four boundaries (`TB-CAPABILITY`,
`TB-CONTAINER`, `TB-BOOT`, `TB-AI`) receive deeper treatment in Phases
3–6 respectively — this document's job is a first honest pass across all
ten, not the final word on any of them.

## 3. Finding ID Scheme

Findings use `FIND-<TB-SHORT>-<NNN>`, where `<TB-SHORT>` drops the `TB-`
prefix from NPS-018 §4 (e.g. `FIND-KERNEL-001`). IDs are permanent per
the same reasoning as document and requirement IDs (ADR-0017, NPC-009
§3.2) — a finding that's resolved gets marked `Resolved`, not deleted.

## 4. `TB-KERNEL` — Kernel Space ↔ User Space

Surface: `SURFACE-GPU-0001` (Vulkan command buffer submission, the one
kernel-space exception NPS-001 §3 carves out for performance).

| ID | STRIDE | Finding | Severity (Impact/Likelihood) | Existing Mitigation | Status |
|----|--------|---------|-------------------------------|------------------------|--------|
| `FIND-KERNEL-001` | Tampering / Elevation of Privilege | A malicious or buggy container could submit a crafted Vulkan command buffer designed to exploit a GPU driver parsing bug, potentially achieving kernel-adjacent memory corruption. This is a well-documented vulnerability class in real GPU driver stacks, not a hypothetical. | High / Medium | None specified — NPS-001 §3 designates this a kernel-space fast path *for performance* but never states a validation requirement | **Open — spec gap, see §9** |
| `FIND-KERNEL-002` | Information Disclosure | GPU memory (textures, buffers) not explicitly cleared between containers using the same physical GPU resources could leak data across the `TB-CONTAINER` boundary via the GPU. | Medium / Medium | None specified | Open |
| `FIND-KERNEL-003` | Denial of Service | A command buffer that hangs the GPU (infinite shader loop, malformed descriptor) denies GPU access to every other container until recovery. | High / Medium | None specified — no timeout/preemption requirement exists | Open |

## 5. `TB-CONTAINER` — Container ↔ Container

Surfaces: `SURFACE-IPC-0001`, `SURFACE-IPC-0002`. (Deep dive: Phase 4.)

| ID | STRIDE | Finding | Severity | Existing Mitigation | Status |
|----|--------|---------|----------|------------------------|--------|
| `FIND-CONTAINER-001` | Elevation of Privilege | If the kernel's capability-transfer validation (NPS-003 §5.2 — "MUST NOT transfer a capability broader than it holds") has an implementation bug, a container could escalate via a forged or widened capability transfer. | High / Low (implementation-dependent, not yet built) | NPS-003 §5.2, §5.4 (kernel as sole arbiter) | Open — tracked as an implementation-testing requirement, not a spec gap |
| `FIND-CONTAINER-002` | Denial of Service | Endpoint flooding to exhaust another container's IPC processing capacity. | Medium / Medium | ADR-0009 (token-bucket rate limiting) | **Mitigated in spec, unverified in practice** — same benchmark gate already tracked in `tests/BENCHMARK_PLAN.md` §3 |
| `FIND-CONTAINER-003` | Information Disclosure | A shared-memory region (NPS-003 §3.1, bulk transfer path) left mapped after a container's teardown (NPS-002 §5.6) could be reused by a new container before being cleared, leaking the prior container's data. | High / Medium | NPS-002 §5.6 requires resource reclamation but does not explicitly require zeroing shared memory before reuse | **Open — spec gap, see §9** |

## 6. `TB-CAPABILITY` — Granted vs. Ungranted Access

Surfaces: `SURFACE-CAP-0001`, `SURFACE-CAP-0002`. (Deep dive: Phase 3.)

| ID | STRIDE | Finding | Severity | Existing Mitigation | Status |
|----|--------|---------|----------|------------------------|--------|
| `FIND-CAPABILITY-001` | Elevation of Privilege | A manifest requesting an undefined capability must be rejected (NPS-010 §4.2) — but the finding here is narrower: what happens to a manifest requesting a capability that *was* well-defined at submission time but is *deprecated/removed* between submission and evaluation? No such race is specified. | Low / Low (requires a capability removal event, which hasn't happened yet in the registry's history) | NPS-010 §4.2 (undefined-capability rejection) partially covers this | Open, low priority — full analysis deferred to Phase 3 |
| `FIND-CAPABILITY-002` | Repudiation | NPS-010 §8.1 requires grants/revocations be recorded in a user-inspectable form, but doesn't specify the audit record itself is tamper-evident (append-only, signed, etc.). A compromised container with write access to the audit store could retroactively alter its own history. | Medium / Low | NPS-010 §8.1 (existence of the record), no integrity requirement | Open — full analysis deferred to Phase 3 |

## 7. `TB-BACKEND` — NyCore Contracts ↔ NyHAL Backend Implementation

Surface: `SURFACE-BACKEND-0001`.

| ID | STRIDE | Finding | Severity | Existing Mitigation | Status |
|----|--------|---------|----------|------------------------|--------|
| `FIND-BACKEND-001` | Elevation of Privilege | This is the boundary the `nyctr` PoC (`source/nyhal-linux-backend/poc-container/`) already documents honestly: unprivileged Linux user-namespace escapes are a real, recurring CVE class, and the PoC implements **zero** capability enforcement (no seccomp, no LSM policy) — it relies entirely on namespace isolation, which is not the same guarantee NPS-017 §4.2 requires ("the backend SHALL be the sole arbiter of capability validity"). | High / High (this is the current, real state of the only code that exists) | NPS-017 §4.2, §5.1 (conformance requirement exists; the PoC explicitly does not claim to meet it) | **Not a spec gap — an implementation gap already tracked**: `REQ-NYHAL-0003` in the Requirements Database is exactly this, marked `Implemented (partial)` with the capability-enforcement gap named explicitly in its caveat |

## 8. `TB-COMPAT-RUNTIME`, `TB-NETWORK`, `TB-PACKAGE`, `TB-EMULATOR` — Remaining Boundaries

Surfaces: `SURFACE-WIN-*`, `SURFACE-AND-*`, `SURFACE-NET-*`, `SURFACE-SYNC-0001`, `SURFACE-FS-0001`, `SURFACE-EMU-0001`.

| ID | Boundary | STRIDE | Finding | Severity | Existing Mitigation | Status |
|----|----------|--------|---------|----------|------------------------|--------|
| `FIND-COMPAT-001` | `TB-COMPAT-RUNTIME` | Tampering / Elevation | A malformed `.exe`/`.msi` or `.apk` could exploit a parsing bug in the translation layer itself (Win32 API translation, DirectX-to-Vulkan translation, the AOSP container runtime) — legacy-compatibility code has a well-documented history of parser vulnerabilities (see e.g. Wine/Proton's own CVE history as a reasonable proxy for this class of risk). | High / Medium | Container isolation (ADR-0004) limits blast radius to the compat-runtime container itself, not the whole system | Open — inherent to the approach, mitigated by containment rather than eliminated |
| `FIND-NETWORK-001` | `TB-NETWORK` | Tampering / Information Disclosure | Neither NPS-011 (capability registry) nor NPS-016 (cloud sync) specifies that `CAP-NETWORK`/`CAP-CLOUD-SYNC` traffic must be encrypted in transit for *general* application network access — NPS-016 §7.1 requires it for sync specifically, but general `CAP-NETWORK` use by an arbitrary application has no such requirement. | Medium / Medium | NPS-016 §7.1 (sync-specific only) | **Open — spec gap, narrower than it looks: general app network traffic encryption is an application-layer concern (e.g. HTTPS) outside the OS's control in most cases, but the OS should not actively prevent or complicate TLS use; no finding requires immediate action beyond noting the boundary** |
| `FIND-PACKAGE-001` | `TB-PACKAGE` | Spoofing / Tampering | `NPS-006` §3.1 specifies a manifest with **checksums** for integrity verification, but checksums alone don't establish *authenticity* — an attacker who tampers with a `.nygi` image can simply recompute the checksum for their modified content. There is no package-signing / publisher-identity specification anywhere in the current document set. | **High / High** | NPS-006 §6 (checksum verification only — verifies integrity against self-consistency, not against a trusted publisher) | **Open — significant spec gap, see §9** |
| `FIND-EMU-001` | `TB-EMULATOR` | Tampering / Elevation | User-supplied ROM/BIOS files are exactly the kind of legacy, adversarial-input-prone format (emulator cores are frequently older C codebases) that historically produces memory-corruption vulnerabilities in the emulator itself. | High / Medium | NPS-014 §5.1 (each emulator runs in its own container), §5.2 (scoped capabilities only) | Mitigated by containment (same pattern as `FIND-COMPAT-001`) — acceptable given NPS-014 §3's framing that Nythera doesn't control the emulator cores' own code quality |

## 9. High-Severity Findings Requiring a Tracked Follow-Up

Per NPS-018 §6, every `High`/`High` or `High`-impact finding needs a
resolution path, not just a row in this table. Three genuine spec gaps
were found:

1. **`FIND-KERNEL-001`/`FIND-KERNEL-003`** (GPU command buffer validation
   and hang prevention) — resolved by revising **NPS-001** to add an
   explicit requirement in this same change (§10 below), plus a new
   `REQ-GPU-0002`.
2. **`FIND-CONTAINER-003`** (shared-memory reuse without clearing) —
   resolved by revising **NPS-003** to add an explicit zeroing
   requirement in this same change (§10 below), plus a new `REQ-IPC-0003`.
3. **`FIND-PACKAGE-001`** (no package signing / publisher authenticity) —
   **not** resolved by a quick amendment; this needs a real specification
   (digital signatures, PKI/trust model), which is already tracked as
   Milestone 11 gap category 7 ("Package format specification... digital
   signatures"). This finding is the concrete justification for treating
   that gap category as higher priority than its list position suggests
   — tracked explicitly in `007-PROJECT_ROADMAP.md`, not silently left as
   a threat-model observation with nowhere to go.

`FIND-BACKEND-001` scores `High`/`High` but is explicitly **not** a new
finding requiring new tracking — it's the threat model independently
arriving at exactly what `REQ-NYHAL-0003` and the `nyctr` PoC's own README
already say. That convergence is itself a useful signal: the existing
honesty about the PoC's limitations held up under a structured threat
analysis, rather than turning out to be understated.

## 10. Immediate Specification Amendments

Two `Draft` specifications are amended directly here since the fixes are
small, uncontroversial, and don't require an ADR-level design decision —
consistent with how ADR-0014's secure-boot resolution was folded directly
into NPS-001 §7 in Milestone 10.

**NPS-001** (Kernel Architecture and Boot) — add to §3 (Kernel-Space
Components), GPU Command Submission Path row: the fast path **MUST**
validate command buffer structure before execution and **MUST** enforce a
submission timeout with preemption, to close `FIND-KERNEL-001` and
`FIND-KERNEL-003`.

**NPS-003** (Inter-Process Communication and Capability Passing) — add to
§3.1: shared-memory regions granted for bulk transfer **MUST** be cleared
(zeroed) before being made available to a different container, to close
`FIND-CONTAINER-003`.

Both amendments are applied to those documents directly (see their own
revision history for the corresponding entries) rather than only
described here, per NPS-018 §8's rule against the threat model becoming a
shadow source of truth.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-07-13 | Initial draft — Phase 2 of the threat model (STRIDE analysis, all 10 trust boundaries) |

---
**End of Document**
