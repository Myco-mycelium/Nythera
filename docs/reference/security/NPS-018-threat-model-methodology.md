---
title: Threat Model Methodology and Trust Boundaries
document_id: NPS-018
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
depends_on: [NTM-000, NPC-001, NPC-009, ADR-0004, ADR-0006, NPS-002, NPS-003, NPS-010, NPS-011]
---

# NPS-018 — Threat Model Methodology and Trust Boundaries

## 1. Status of This Document

This document is **normative**. RFC 2119 terms apply as in NPC-001. It is
**Phase 1** of the Nythera threat model, defining the methodology and
trust boundary map that every later phase (attack surface enumeration,
STRIDE analysis, privilege escalation analysis, container escape
analysis, secure boot, AI, and package trust models) is built against.
This document does not itself contain a completed threat analysis — it
defines how one is done and where the boundaries are, so later phases are
consistent with each other rather than independently inventing framing.

## 2. Scope

This specification covers: the threat modeling framework (STRIDE,
applied), the canonical list of trust boundaries in the system, attacker
profiles, and a severity scoring model. Subsequent phases (tracked in
`007-PROJECT_ROADMAP.md` Milestone 12) apply this framework to specific
subsystems and produce findings. Findings that require a design change
**MUST** flow back into an ADR or NPS revision (NPC-001 §6), not live only
in a threat-model document as an unaddressed observation.

## 3. Framework: STRIDE

Nythera uses **STRIDE** for structured threat identification. Each trust
boundary (§4) is analyzed for:

| Category | Question asked |
|----------|------------------|
| **S**poofing | Can an entity convincingly claim to be something it isn't (another container, another user, a trusted package publisher)? |
| **T**ampering | Can data or code be modified without authorization (in transit over IPC, at rest on NyFS, in a mounted `.nygi` image)? |
| **R**epudiation | Can an entity deny having performed an action the system needs to hold it accountable for (a capability grant, an AI-suggested action the user approved)? |
| **I**nformation Disclosure | Can data cross a trust boundary it shouldn't (one container reading another's memory, a capability leaking data beyond its stated scope)? |
| **D**enial of Service | Can an entity degrade or block service for others (IPC flooding, resource exhaustion, cgroup limit bypass)? |
| **E**levation of Privilege | Can an entity gain a capability or access level it wasn't granted (capability forgery, container escape, sandboxed process escaping its namespace)? |

Each STRIDE finding in later phases **MUST** record: the trust boundary
it applies to (§4), which STRIDE category, a description, a severity
(§6), the existing mitigating control if any (cited by NPS/ADR section),
and a `REQ-SEC-*` or `REQ-CAP-*` requirement ID (NPC-009) if a concrete,
testable obligation results.

## 4. Trust Boundaries

Nythera's trust boundaries, derived from decisions already made rather
than invented fresh for this document:

| ID | Boundary | Crossed by | Governing spec |
|----|----------|------------|------------------|
| `TB-KERNEL` | Kernel space ↔ user space | Any syscall/IPC primitive reaching kernel-space components | NPS-001 §3, ADR-0006 |
| `TB-CONTAINER` | Container ↔ container | Any IPC `send`/`call`, shared memory grant, or capability transfer between containers | ADR-0004, NPS-002 §4, NPS-003 |
| `TB-CAPABILITY` | Granted vs. ungranted access within a single container | Any operation gated by a `CAP-*` capability | NPS-010 §4–§5, NPS-011 |
| `TB-BACKEND` | NyCore contracts ↔ NyHAL backend implementation | Any point where a backend's native mechanism (Linux namespaces/cgroups/seccomp, or a future NyKernel primitive) is trusted to actually enforce a NyCore guarantee | NPS-017 §4 |
| `TB-COMPAT-RUNTIME` | Native trust model ↔ translated Windows/Android code | Any `.exe`/`.msi`/`.apk` executing through NPS-007 or NPS-008 | ADR-0005, ADR-0008, NPS-007, NPS-008 |
| `TB-NETWORK` | Local system ↔ network | Any traffic under `CAP-NETWORK`/`CAP-NETWORK-LISTEN`/`CAP-CLOUD-SYNC` | NPS-011, NPS-016 |
| `TB-BOOT` | Firmware/physical access ↔ running system | Boot loader verification, Secure Boot key enrollment | NPS-001 §5, ADR-0014 |
| `TB-PACKAGE` | Package publisher/source ↔ installed, running software | `.nygi` image creation and verification | NPS-006 §3, §6 |
| `TB-AI` | AI assistant container ↔ rest of the system | `CAP-AI-DIAGNOSTICS-READ`, `CAP-AI-SUGGEST-ACTION` | NPS-015 §4–§5 |
| `TB-EMULATOR` | User-supplied ROM/BIOS content ↔ emulator container | Emulator Hub file parsing | NPS-014 §5–§6 |

This table **MUST** be updated when a new subsystem introduces a boundary
not already covered — e.g. a future package format specification (Milestone
11 gap category 7) will likely refine `TB-PACKAGE` rather than replace it.

## 5. Attacker Profiles

Later phases **MUST** state which of these profiles a given finding
assumes, since mitigations differ by what the attacker can already do:

| Profile | Capability |
|---------|------------|
| **Unprivileged local application** | Runs as an ordinary container with only its granted capabilities (NPS-010). No physical access, no elevated grants. |
| **Malicious/compromised compat-runtime application** | A `.exe`/`.msi`/`.apk` that is itself hostile, or a legitimate one compromised after install, running inside NPS-007/NPS-008's translation layer. |
| **Network attacker** | No local presence; interacts only via `TB-NETWORK` (traffic to/from a container holding network capabilities, or the cloud sync endpoint). |
| **Malicious package publisher** | Can produce a `.nygi` image or APK that a user might choose to install; does not yet have any capability grant. |
| **Physical attacker** | Has physical access to powered-off or powered-on hardware; relevant to `TB-BOOT` and disk-at-rest scenarios. |
| **Compromised AI assistant container** | The AI assistant's own container (NPS-015) has been compromised via a supply-chain or model-level issue; relevant to `TB-AI`. |

A profile **MUST NOT** be assumed to already hold a capability it hasn't
been granted — per NPS-010 §4.2, capability grants are evaluated, not
assumed, and the threat model follows the same discipline: "what if this
attacker already had X" is a different, separately-labeled question from
"can this attacker obtain X."

## 6. Severity Model

Each finding **MUST** be scored on two axes, kept deliberately simple
rather than adopting a full CVSS calculation this project has no
data to calibrate yet:

- **Impact**: `Low` (limited to the attacker's own container),
  `Medium` (affects other containers or user data within one trust
  boundary), `High` (crosses `TB-KERNEL`, `TB-CAPABILITY`, or `TB-BOOT`,
  or affects multiple users/containers system-wide).
- **Likelihood**: `Low`, `Medium`, `High` — a qualitative judgment
  pending real implementation and testing; **MUST NOT** be presented as a
  measured probability (consistent with NPC-002 §5.2's ban on unverified
  performance/security claims).

Findings scoring **High** on either axis **MUST** produce a tracked
follow-up (an ADR, an NPS revision, or a `REQ-SEC-*`/`REQ-CAP-*` entry)
before the corresponding phase document can move past `Draft`.

## 7. Non-Goals *(Informative)*

This methodology does not cover: physical hardware supply-chain security
(out of scope for a software specification project), legal/compliance
threat modeling (e.g. GDPR data-handling obligations — a separate future
concern), or third-party dependency vulnerability tracking (an ongoing
operational process once real dependencies exist, not a one-time design
document).

## 8. Relationship to Other Specifications

This document **MUST NOT** duplicate normative requirements already
stated elsewhere (NPS-002, NPS-003, NPS-010, NPS-011, etc.); it **MUST**
cite them. Threat model findings that reveal a gap in an existing
specification **MUST** be resolved by revising that specification (NPC-001
§6), with the threat-model document recording the finding and pointing at
the resolution — not by the threat model silently becoming the source of
truth for a requirement that belongs elsewhere.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-07-13 | Initial draft — Phase 1 of the threat model (methodology and trust boundaries) |

---
**End of Document**
