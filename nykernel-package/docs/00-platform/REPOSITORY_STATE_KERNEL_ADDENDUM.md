# REPOSITORY_STATE.md addendum: `source/nykernel/` bring-up

**Status: draft, not yet merged.** This AI-assisted session has no
push access to the actual repository (see `engineering/RFC-0001-nykernel-bringup-plan.md`
§6), so this file is written to be merged into the real
`docs/00-platform/REPOSITORY_STATE.md` by whoever has that access,
rather than being an already-applied edit. It's formatted to slot into
that file's existing "Source Code" section pattern (see the
`nyhal-linux-backend` entry there) as a sibling entry for
`source/nykernel/`. `CHANGE_REQUEST_LOG.md`'s exact current format
wasn't available to check against in this session (see the note at the
end) -- treat that part as content to reconcile against the real
file's structure, not a verified-matching snippet.

---

## Proposed addition to REPOSITORY_STATE.md's "Source Code" section

**`source/nykernel/`** -- a real, boot-verified x86_64 kernel.
Multiboot2-bootable, Rust (`no_std`) plus hand-written x86_64 assembly
for the boot stub, built against `NPS-001`/`NPS-017` (both Accepted)
and `ADR-0006`/`ADR-0012`/`ADR-0020` (all Accepted; `ADR-0020`'s
Rust/C++/C rule is honored with zero Python anywhere in this
component -- unlike `nyhal-linux-backend`, this *is* the
platform-critical path that rule was written about).

Self-rated (see `source/nykernel/IMPLEMENTATION_STATUS.md`):
**Experimental -- Phase 1 (Boot) and Phase 2 (Memory Management)
Complete and Verified against NPS-001/017. Ten additional, explicitly
pre-normative Phase 5/6 (Windows/Android compatibility) kernel-level
prerequisites Complete and Verified, isolated in `src/experimental/`
with an explicit "not NPS-002/NPS-003 conformance" banner (both specs
remain `Draft` per `NPC-004`).**

What's real, each independently boot-verified via
`tools/qemu_boot_test.sh` (automated pass/fail exit code, not manual
inspection), most with an accompanying negative-control test:

- Multiboot2 boot, long-mode transition, GDT/TSS, IDT with real CPU
  exception handlers, an explicit boot-phase state machine implementing
  NPS-001 §5's order-validation requirement
- Physical frame allocation, dynamic page-table mapping, a working
  kernel heap
- PIC remap + real hardware timer interrupts, plus a second real IRQ
  line: the CMOS/RTC periodic interrupt (IRQ8, exercising the
  slave-PIC cascade path)
- ACPI RSDP validation and RSDT/XSDT table enumeration, with the frame
  allocator refusing `AcpiReclaimable` regions until `reclaim_acpi`
  releases them (no QEMU machine type reports such a region, so the
  release contract is proven by a labelled synthetic control)
- Guard pages for stack-overflow detection (verified every boot with a
  real overflow write that must fault, and a negative control), now
  also backing every live kernel stack -- the ring-3 tasks' RSP0 stacks
  and the cooperative scheduler's ring-0 task stacks -- via a shared
  guarded-stack region (`memory::kernel_stack`), verified structurally
  every boot
- NX (no-execute) enforcement -- verified with a positive control
  (a deliberate execution attempt from a non-executable page, correctly
  faulted)
- Cooperative kernel-mode task scheduling + inter-task IPC (mailboxes)
- Real ring-3 (user-mode) execution, verified via CPU-reported
  privilege level, not assumed
- A minimal ELF64 loader -- both fixed-address (`ET_EXEC`) and
  position-independent (`ET_DYN`, with real `R_X86_64_RELATIVE`
  relocation processing)
- Per-process address-space isolation, integrated into the cooperative
  scheduler (50 real CR3 switches per task verified) and into real
  ring-3 task scheduling (a hand-written full-general-purpose-register
  trap handler, now three ring-3 tasks and 21 real trap-based yields
  verified)
- Real address-space teardown: a free-list-backed physical frame
  allocator plus full page-table-tree walking, verified by an
  unambiguous frame-reuse proof (a torn-down space's exact freed PML4
  frame reused by the next space built, compared by physical address);
  and now exercised on a space that was ever *active* for a ring-3 trap
  (task F in `ring3_scheduler`, destroyed after suspension, 15 frames
  reclaimed -- including its now frame-allocator-backed guarded stack)
- TLB invalidation (`invlpg`) on unmap, proven by a map/unmap/remap
  self-test that reads back the new backing frame
- A minimal PE32+ (Windows) loader, including base-relocation
  processing (DIR64/HIGHLOW), proven by running a relocatable PE at a
  loader-chosen address; and a minimal DEX (Android) container parser
  with in-kernel Adler-32/SHA-1 integrity verification

Several real bugs were found and fixed during this work, each
documented with root cause in `IMPLEMENTATION_STATUS.md` rather than
just noted as resolved -- consistent with this repo's existing
convention for `nyhal-linux-backend`'s own bug log. Several negative
controls produced results *different* from what was predicted (failing
louder or earlier than the anticipated failure mode) -- documented as
they actually happened.

**Explicitly NOT done, stated plainly rather than implied:** real
dynamic linking (symbol-resolved relocations), preemptive (as opposed
to cooperative) ring-3 task scheduling, and -- by far the
largest remaining gap -- real application compatibility on top of the
loaders (a Win32 API/import layer, a DEX/ART bytecode runtime, the
actual API surfaces). Scheduling/IO policy beyond the pre-normative
mechanisms stays blocked on NPS-002/NPS-003 remaining `Draft`, and
UEFI/Secure Boot on ADR-0014 remaining `Proposed`. See `PHASES.md`
(repo root) for the full phase-by-phase breakdown and `PHASES.md`
Phase 5's explicit Windows/Android scope statement.

**Outstanding action item, unresolved across this entire session, not
silently dropped:** whether `NPS-007`'s current Accepted text already
states the Windows kernel-mode-driver exclusion boundary precisely (per
direct project direction: every ordinary Windows PC application must
run; only software requiring actual Windows kernel-mode execution --
drivers, kernel-mode anti-cheat/DRM -- is excluded). This session's
tooling could not retrieve the document's full body. Do not treat this
as checked.

---

## Proposed CHANGE_REQUEST_LOG.md entry

*(Format not verified against the real file -- reconcile before
merging.)*

**Change:** Kernel bring-up, `source/nykernel/` (new).
**Subsystem:** core-architecture (per `NPC-008`, currently Unassigned
-- filed without a subsystem owner's sign-off, same precedent as
`nyhal-linux-backend`).
**Specs/ADRs relied on (all Accepted, none newly proposed):** NPS-001,
NPS-017, ADR-0006, ADR-0012, ADR-0020.
**Specs explicitly NOT treated as binding (both remain Draft):**
NPS-002, NPS-003 -- see `src/experimental/mod.rs`'s banner.
**Manifest-alignment check:** not performed by a human Architecture
Group (none currently exists for this project) -- see
`engineering/RFC-0001-nykernel-bringup-plan.md` for the AI-session
equivalent of this check.
**Evidence:** every claim boot-verified via `tools/qemu_boot_test.sh`;
see `source/nykernel/IMPLEMENTATION_STATUS.md` for full transcripts.
