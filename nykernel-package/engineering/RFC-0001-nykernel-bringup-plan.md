# RFC-0001: Nyrqis Kernel Bring-Up Plan

**Status:** Draft (engineering working note, not a normative NPC/ADR/NPS)
**Subsystem:** core-architecture (per NPC-008, currently **Unassigned** --
this RFC is filed without a subsystem owner's sign-off, same precedent
as the Linux backend PoC, and should be reconciled once a real owner
exists)
**Author:** AI-assisted contribution, unreviewed by a human Architecture
Group (the project currently has zero active human contributors --
see README "All subsystems ... Unassigned")

## 1. Purpose

Per NPC-001 (Project Constitution) §3.1, the kernel is an explicitly
named cross-cutting subsystem requiring Architecture Group approval for
changes. Per §6 (Change Process), a proposal must checked for
Manifest-alignment, tagged to a subsystem owner, and reflected in
`SPECIFICATION_INDEX.md` / `REPOSITORY_STATE.md` / `CHANGE_REQUEST_LOG.md`
in the same changeset as any resulting work. This document is that
proposal, filed as an `engineering/` working note (per the repository
layout table in `README.md`) rather than a new ADR or NPS, because it
does not introduce a new binding decision -- it operationalizes ones
that are already `Accepted`.

## 2. What's already settled (binding, cited, not re-litigated here)

- **NPS-001** (Kernel Architecture and Boot) -- Accepted, v1.2.0. §5
  requires boot-phase transitions to be order-validated (a Milestone-10
  threat-model finding); §3 describes kernel-space vs user-space
  component placement.
- **NPS-017** (NyHAL -- Kernel Abstraction Layer and Backend Contract)
  -- Accepted. Defines the backend contract this kernel is a second
  implementation of (the first being the existing Linux userspace
  backend at `source/nyhal-linux-backend/`).
- **ADR-0006** (hybrid microkernel as kernel base) -- Accepted.
- **ADR-0012** (NyHAL pluggable kernel abstraction layer) -- Accepted.
- **ADR-0020** (implementation languages and the platform boundary) --
  Accepted. Rust/C++/C for platform-critical paths; Python explicitly
  barred. This kernel is written entirely in Rust (`no_std`) plus a
  small hand-written x86_64 assembly boot stub, per that rule --
  unlike the Linux backend, there is no Python anywhere in this
  component, because this component *is* the platform-critical path
  ADR-0020 was written about.

## 3. What's explicitly NOT settled yet (Draft; not built against as binding)

- **NPS-002** (Process and Thread Model) -- Draft, benchmark-blocked
  (§9). Phase 3 (scheduling) work will be built as an **explicitly
  flagged pre-normative technology proof**, not as NPS-002 conformance,
  until that spec is Accepted. Building a "conformant" scheduler
  against a Draft spec would misrepresent both the code and the spec's
  status, which NPC-001 §5 (Draft = not binding) exists to prevent.
- **NPS-003** (IPC and Capability Passing) -- Draft, transport
  pending (§6.1). Same treatment for Phase 4 (kernel IPC).
- **ADR-0013** (EEVDF scheduler) -- Proposed, tuning-blocked.
- **ADR-0014** (UEFI Secure Boot) -- Proposed. Phase 1's boot path
  (BIOS/Multiboot2 via GRUB) is a deliberately simpler bring-up target
  that does not depend on ADR-0014 either way; UEFI + Secure Boot
  enrollment is out of scope until that ADR is Accepted.

## 4. New: Windows and Android compatibility are kernel-level scope

Per direct project direction (2026-08, this session): Windows and
Android application compatibility are **not** an optional userspace
add-on bolted on after the fact -- they are part of the kernel's own
design scope, alongside NPS-007 (Windows Compatibility Runtime,
Accepted) and NPS-008 (Android Compatibility Runtime, Accepted). The
concrete acceptance bar, stated precisely so it's checkable rather than
aspirational:

- **Android:** the full application compatibility target -- an
  installed `.apk`'s app is expected to run, full stop. No carve-out.
- **Windows:** the full application compatibility target for
  everything *except* software whose correct operation genuinely
  requires operating at Windows' own kernel level (kernel-mode
  drivers, anti-cheat/DRM systems that install a WDM/WDF driver,
  low-level filter drivers, and similar). This covers ordinary desktop
  and PC applications (`.exe`/`.msi` userspace software) without
  exception -- the exclusion is specifically and only for software
  that would require Nyrqis to host actual Windows kernel-mode driver
  execution, which NPS-007's translation-runtime approach (a
  WINE-shaped userspace compatibility layer per ADR-0005) does not
  attempt and is not expected to.

**Action item, not yet done:** confirm whether NPS-007's current
Accepted text already states this Windows kernel-mode exclusion
precisely, or whether it needs a formal amendment (version bump +
revision-history entry per NPC-001 §5) to make this boundary explicit
and checkable rather than implicit. Tracked in `PHASES.md` Phase 5.
This RFC does not itself amend NPS-007 -- that requires reading its
full current text first, which is future work.

## 5. Bring-up toolchain (the actual "how", operationalizing NPS-001)

Documented in full in `source/nykernel/IMPLEMENTATION_STATUS.md`.
Summary: Rust (`no_std`, `staticlib`) for everything after long-mode
entry, hand-written x86_64 assembly (GNU `as`) for the pre-long-mode
Multiboot2 boot stub, GNU `ld` with a custom linker script, GRUB2 +
`grub-mkrescue` for a real bootable ISO, QEMU for boot verification
(both interactive and an automated pass/fail harness via the
isa-debug-exit device). The build runs on the host's stable Rust
toolchain plus the `rust-src` component and `RUSTC_BOOTSTRAP=1` in place
of an actual nightly toolchain, to unlock `cargo build-std`. During
bring-up that was Ubuntu's `apt` Rust 1.75; as of the 2026-10-08
migration the kernel targets rustup stable (rustc 1.97.1) -- see
IMPLEMENTATION_STATUS.md §"Toolchain notes" for the current setup and
the full list of source/spec changes the compiler upgrade required.

## 6. Required same-changeset updates (per NPC-001 §6.5 pattern)

The following are drafted as separate files/snippets for the repo
maintainer to merge (this AI has no push access to the actual repo):

- `source/nykernel/IMPLEMENTATION_STATUS.md` (new)
- `source/nykernel/README.md` (new -- an orientation entry point;
  `IMPLEMENTATION_STATUS.md` grew into a detailed evidence log across
  the session's many increments and stopped being a good first read)
- `PHASES.md` (new, top-level -- the person-readable phase tracker
  requested directly, kept outside `docs/` since it's a working
  tracker rather than a normative spec)
- `docs/00-platform/REPOSITORY_STATE_KERNEL_ADDENDUM.md` (new --
  drafted in a later pass of this session, once the kernel work had
  progressed far enough to summarize meaningfully. Contains a proposed
  addition to `REPOSITORY_STATE.md`'s "Source Code" section and a
  proposed `CHANGE_REQUEST_LOG.md` entry. **Still not merged** -- no
  push access -- and the `CHANGE_REQUEST_LOG.md` entry's format
  specifically is unverified against the real file, since this
  session never obtained that file's actual current content. This
  closes the "outstanding" status these two items carried since RFC-0001
  was first drafted, but does not mean they're actually in the repo
  yet -- that still requires a human (or a session with real repo
  write access) to apply them.

