# Nyrqis Kernel -- Phase Tracker

A single place to see kernel implementation progress at a glance. Kept
at the repo root (not under `docs/`) because it's a working tracker,
not a normative spec -- normative status still lives in
`docs/00-platform/REPOSITORY_STATE.md` and `NPC-004 Specification
Index`. Update this file's status column in the same change that
completes or starts a phase.

**Legend:** ✅ Done & verified · 🚧 In progress · ⛔ Blocked · ⬜ Not started

**Design/UI convention (applies from this point forward, 2026-08):**
any design or UI-related feature work in this kernel's scope --
notably `src/experimental` and future kernel-native NyUI/shell surfaces
(Phase 9), and the Windows/Android compatibility layers' visual/UX
surfaces (Phase 5/6) -- follows Apple's Human Interface Guidelines
(https://developer.apple.com/design/human-interface-guidelines/).
This governs interaction/visual design decisions specifically; it does
not change any NPS-009 (Adaptive UI Shell) technical requirements,
which remain the binding spec for that subsystem regardless of visual
convention. Nothing in Phases 0-3 is UI-bearing, so this has no effect
yet -- noted here so it's not lost by the time Phase 9 starts.

| Phase | Name | Status | Depends on (spec status) |
|---|---|---|---|
| 0 | Documentation gate | ✅ Done | NPC-001 §3.1/§6 |
| 1 | Boot to a running kernel | ✅ Done & verified | NPS-001 (Accepted), NPS-017 (Accepted) |
| 2 | Memory management | ✅ Done & verified | NPS-001 (Accepted) |
| 3 | Scheduling / processes | ✅ Pre-normative proof done & verified | NPS-002 (**Draft** — pre-normative only) |
| 4 | Kernel IPC | ✅ Pre-normative proof done & verified | NPS-003 (**Draft** — pre-normative only) |
| 5 | Windows compatibility | 🚧 Prerequisites done; first real capability (PE32+ loader) landed | NPS-007 (Accepted; scope confirmation needed — see below) |
| 6 | Android compatibility | 🚧 First real capability (DEX format parser) landed | NPS-008 (Accepted) |
| 7 | GPU / graphics kernel path | ⬜ Not started | NPS-001 §3, NPS-013 (Accepted) |
| 8 | Storage integration | ⬜ Not started | NPS-004 (Accepted), NyVault (source/nyhal-linux-backend precedent) |
| 9 | Gaming subsystem / multi-device | ⬜ Not started | NPS-006, NPS-012, NPS-014 (Accepted) |
| 10 | Secure Boot / UEFI | ⬜ Not started | ADR-0014 (**Proposed**, not yet Accepted) |

---

## Phase 0 -- Documentation gate ✅

Per NPC-001 §3.1, the kernel is a cross-cutting subsystem requiring
Architecture Group review; per §6, a change needs Manifest-alignment
checking and same-changeset updates to the specification index and
repository state. Since this project currently has no active human
Architecture Group, this phase's output is the honest paper trail
rather than an actual approval:

- `engineering/RFC-0001-nykernel-bringup-plan.md` -- proposal, spec
  citations, explicit call-out of what's Accepted vs. Draft
- `source/nykernel/IMPLEMENTATION_STATUS.md` -- self-rated status,
  matching the `nyhal-linux-backend` convention
- This file

**Outstanding from Phase 0** (not yet done, flagged honestly): a
`REPOSITORY_STATE.md` entry and a `CHANGE_REQUEST_LOG.md` entry
recording that kernel bring-up has started. Needs this session's
actual current content of those files, which hasn't been fetched yet.

## Phase 1 -- Boot to a running kernel ✅ Done & verified

**Built and verified with a real QEMU boot**, not simulated:

- Multiboot2 boot header + 32-bit protected mode entry (`boot/boot.s`)
- CPUID / long-mode support checks, with distinct VGA error codes on
  failure (before any Rust or serial diagnostics exist)
- Identity-mapped paging (first 1GiB, 2MiB pages), long mode entry
- Kernel GDT + TSS with a dedicated double-fault IST stack (`src/gdt.rs`)
- IDT with real handlers: divide-error, invalid-opcode, GPF, page
  fault, double fault (`src/idt.rs`)
- 16550 UART serial diagnostics (`src/serial.rs`)
- Explicit boot-phase state machine implementing NPS-001 §5's
  order-validation requirement (`src/boot_phase.rs`)
- Automated pass/fail boot test via QEMU's isa-debug-exit device
  (`tools/qemu_boot_test.sh`)

**Explicitly out of scope for Phase 1** (see IMPLEMENTATION_STATUS.md):
memory allocator, higher-half layout, PIC/APIC + hardware interrupts,
scheduling, IPC, storage, UEFI/Secure Boot.

Real bugs hit and fixed during this phase (full detail in
IMPLEMENTATION_STATUS.md): an `x86_64` crate/rustc version mismatch, an
invalid Unicode escape, QEMU's `-kernel` flag not supporting this
ELF64 Multiboot2 binary directly, a missing `SHF_ALLOC` flag stripping
the Multiboot header from the loadable segment, `--gc-sections`
subsequently discarding it anyway, and a PIC/IRQ0-vs-double-fault
vector collision risk closed by masking interrupts for the whole phase.

## Phase 2 -- Memory management ✅ Done & verified

Builds directly on NPS-001 (Accepted); no spec blockers.

**Built and verified with a real QEMU boot:**

- Multiboot2 memory-map parser (`src/multiboot.rs`) -- walks the real
  tag list GRUB hands off, bounds-checked against adversarial/malformed
  `total_size`/tag `size` fields, not just the happy path
- Physical frame allocator (`src/memory/frame_allocator.rs`) -- a bump
  allocator over `Available` regions, correctly excluding the low 1MiB
  legacy area and the kernel image's own physical footprint
  (`_kernel_start`/`_kernel_end`, now emitted by `linker.ld`)
- Dynamic page-table mapping (`src/memory/paging.rs`) -- using the
  `x86_64` crate's `Mapper`/`FrameAllocator` traits, creates new
  intermediate page-table levels on demand rather than requiring them
  to pre-exist
- A working kernel heap (`src/memory/heap.rs`) backed by
  `linked_list_allocator`, mapped at `0x10000000000` -- deliberately
  far outside Phase 1's static identity-mapped first 1GiB, so
  successfully reading/writing through it is real proof the dynamic
  mapper works, not an artifact of already-mapped memory
- A real correctness self-test in `kernel_main`: allocates a
  heap-backed `Vec<u32>`, sums it, asserts the result -- a silent
  memory-management bug would panic or produce a wrong number here,
  not just "compile successfully"

**Verified boot transcript** (`tools/qemu_boot_test.sh`, passing):

```
nyrqis kernel: phase 1 bring-up
  gdt: loaded (kernel code segment + tss)
  idt: loaded (divide-error, invalid-opcode, gpf, page-fault, double-fault)
  double-fault ist stack: index 0
  multiboot: memory map parsed, 32639 usable 4KiB frames (127 MiB)
  paging: dynamic mapper live (CR3 read back, OffsetPageTable constructed)
  heap: mapped 64 pages (256 KiB) at 0x10000000000, global allocator live
  heap self-test: Vec<u32> of 100 elements allocated and summed correctly (5050)
  frames allocated so far: 67
nyrqis kernel: phase 1+2 complete, boot phase = 6 (Ready)
PASS: kernel reached BootPhase::Ready and exited cleanly
```

(67 frames allocated for a 64-page heap request is correct, not a bug:
the extra 3 are new PDPT/PD/PT intermediate page-table frames the
mapper had to create along the way, since `0x10000000000` falls
outside any table Phase 1's static setup built.)

**Explicitly out of scope for Phase 2** (deferred, not forgotten): a
true higher-half address space layout (heap placement is a manually
chosen high address, not a systematic kernel/user split).

*(Three items once listed here as deferred are now done: frame
deallocation -- the frame allocator gained a real free-list, see the
teardown increment below; guard pages for stack-overflow detection --
`memory::guard_demo`; and ACPI-reclaimable memory reuse -- `src/acpi.rs`
validates the RSDP, enumerates the tables, and the allocator only
accepts `AcpiReclaimable` regions after `reclaim_acpi`. See the
2026-10-09 increment in `IMPLEMENTATION_STATUS.md`. The guard-page
mechanism is proven and now guards every live kernel stack (the ring-3
RSP0 stacks and the cooperative scheduler's ring-0 task stacks, both
from `memory::kernel_stack`, verified structurally every boot); this
kernel does not yet *interpret* ACPI tables.)*

## Phase 3 -- Scheduling / processes 🚧, prep done, blocked on NPS-002 remaining Draft

NPS-002 (Process and Thread Model) is `Draft`, benchmark-blocked at
§9 per NPC-004. Per NPC-001 §5, Draft specs aren't binding. This
phase's *scheduling-policy* code, when it starts, must be built and
labeled as an **explicit pre-normative technology proof** -- not
represented as NPS-002 conformance until that spec is Accepted.

**Prerequisite infrastructure is done and verified**, and is
deliberately *not* NPS-002-gated: PIC remapping and hardware timer
interrupts are NPS-001 (kernel architecture) territory, not
NPS-002 (process/scheduling policy) territory -- the distinction
matters, because it means this work didn't need to wait:

- `src/pic.rs` -- 8259 PIC remap (IRQ0-7 -> vectors 0x20-0x27, IRQ8-15
  -> 0x28-0x2F), off the CPU exception vector range that caused a real
  bug during Phase 1 bring-up (IMPLEMENTATION_STATUS.md bug #7)
- Timer interrupt handler wired into the IDT, with correct EOI
  signaling (`src/idt.rs`)
- `sti` executed for the first time anywhere in this kernel, after PIC
  remap + timer handler are both confirmed in place first (order
  matters: handler-then-unmask-then-enable, never the reverse)
- A real correctness proof: `kernel_main` busy-waits (via `hlt`, not a
  hot spin) until 20 real timer interrupts have been observed and
  counted -- a wrong vector mapping, missing EOI, or misrouted handler
  would hang here, not silently "pass"

**More IRQ lines were added later (2026-10-09):** first the CMOS/RTC
periodic interrupt on IRQ8 (`src/rtc.rs`), exercising the slave-PIC
cascade path (IRQ8 reaches the CPU via IRQ2, both PICs unmasked, both
EOI'd); then the PS/2 keyboard (IRQ1, `src/keyboard.rs`) and the
ATA/ATAPI IDE channels (IRQ14/15, `src/ata.rs`). All still NPS-001
territory, not NPS-002-gated. `pic.rs` gained a real `unmask(irq)`. The
RTC self-test waits for a measured *delta* of interrupts after a
snapshot; the keyboard is proven input-free by having the guest inject
its own scancode through the 8042 controller (`0xD2`); the IDE
self-test probes for a device first and reports honestly when none is
present.

**Verified boot transcript** (`tools/qemu_boot_test.sh`, passing, clean
from-scratch build):

```
  pic: remapped (IRQ0-7 -> 0x20-0x27, IRQ8-15 -> 0x28-0x2F), timer unmasked
  rtc: periodic interrupt armed at 1024 Hz, IRQ8 + cascade IRQ2 unmasked
  interrupts: enabled (sti)
  timer self-test: 20 real hardware timer interrupts observed and handled
  rtc self-test: 4 additional RTC periodic interrupts observed and handled after a snapshot (1086 total; IRQ8, via the slave-PIC cascade)
nyrqis kernel: phase 1+2+3-prep complete, boot phase = 7 (Ready)
PASS: kernel reached BootPhase::Ready and exited cleanly
```

**And now, also done: a real context-switch proof, kept explicitly
pre-normative.** `src/experimental/scheduler.rs` (isolated in its own
module, not mixed into `pic.rs`/`idt.rs`, with a "this is not NPS-002
conformance" banner in `src/experimental/mod.rs`) implements a
cooperative, round-robin, stack-switching scheduler:

- A hand-written `#[naked]` `switch_to` function -- saves the current
  task's non-volatile (SysV x86_64 callee-saved) registers and stack
  pointer, restores the next task's, and `ret`s into wherever that
  task was suspended (or, for a never-yet-run task, straight into its
  entry point, via the same stack-layout trick that makes both cases
  handled by identical code)
- `spawn()` builds a fresh task's initial stack frame so its first
  "resume" lands at its entry function as if it had been `call`ed
- Deliberately **cooperative, not preemptive** -- tasks call
  `yield_task()` themselves; it is not wired to the timer interrupt.
  Preemption adds a second class of correctness bug (switching from
  inside an interrupt handler, on the handler's own stack) that a
  first proof-of-concept for "does the switch itself work" doesn't
  need to take on at the same time

**Real correctness proof, verified by a clean from-scratch QEMU boot:**
two tasks each increment their own counter and print once per
activation, then yield; `kernel_main` (itself registered as task 0)
drives exactly 6 round trips. With strict round-robin over 3 tasks,
that means both counters must equal exactly 6 afterward -- a wrong
next-task index, corrupted register state, or bad stack layout would
show up as a wrong count, a hang, or a fault, not a silent false pass:

```
  experimental scheduler: 3 tasks registered (pre-normative, not NPS-002 conformant)
    [experimental task A] activation 1
    [experimental task B] activation 1
    ... (alternates cleanly, A then B, six times each) ...
    [experimental task A] activation 6
    [experimental task B] activation 6
  experimental scheduler self-test: 6 round trips completed, task A ran 6 times, task B ran 6 times
PASS: kernel reached BootPhase::Ready and exited cleanly
```

**Still not started, and still genuinely NPS-002-gated:** any actual
scheduling *policy* -- capability-scoped task isolation, fairness,
preemption tied to the timer tick instead of just counting it, and
anything resembling what NPS-002 will eventually require once it's no
longer Draft. This proof-of-concept should be treated as throwaway
reference material once that happens, not upgraded in place and
relabeled -- see the banner in `src/experimental/mod.rs`.

## Phase 4 -- Kernel IPC ✅ Pre-normative proof done & verified, blocked from real conformance on NPS-003 remaining Draft

Same treatment as Phase 3: NPS-003 is `Draft`, transport-pending at
§6.1. This is the kernel-native counterpart to the IPC layer already
built and tested in `source/nyhal-linux-backend/ipc/` (Unix datagram
sockets, `SCM_CREDENTIALS` auth) -- that implementation is a userspace
NyHAL backend's version of the same contract (NPS-017), not a
substitute for this phase.

**Built and verified**, isolated in `src/experimental/ipc.rs` with the
same "not spec conformance" banner as Phase 3's scheduler:

- A real bounded mailbox per task (`VecDeque<IpcMessage>`,
  heap-allocated, one per task id), with `send`/`try_receive`
- Real backpressure: a full mailbox is a reportable `IpcError`, never
  a silent drop
- Wired into the Phase 3 scheduler demo: task A sends a real message
  `{sender, seq}` to task B's mailbox every activation; task B receives
  and validates both fields

**Real correctness proof, verified by a clean from-scratch QEMU boot:**
6 messages sent, 6 received, every single one with the exact expected
sender id and sequence number, zero mismatches -- a wrong task id, a
race in the mailbox's critical section, or a queue-ordering bug would
show up as a mismatch count or a missing message, not a silent pass:

```
    [experimental task A] activation 6: sent IPC message to task B
    [experimental task B] activation 6: received IPC message {sender: 1, seq: 6} -- OK
  experimental ipc self-test: 6 messages sent task A -> task B and received correctly, 0 mismatches
PASS: kernel reached BootPhase::Ready and exited cleanly
```

**Still not started, and still genuinely NPS-003-gated:** capability-
scoped transfer validation (§5.2 -- a sender must not transfer a
broader capability than it holds), the kernel as sole arbiter of that
validity (§5.4), and the real transport latency benchmark (§6.1) that's
currently blocking NPS-003 from leaving Draft in the first place. This
proof-of-concept's `send`/`try_receive` have none of that -- they're
the mechanical delivery primitive, not the policy layer.

## Phase 5 -- Windows compatibility 🚧, first real capability landed (minimal PE32+ loader); ring-3 + minimal ELF loader + address-space isolation + scheduler integration + real ring-3 task scheduling + NX enforcement + PIE/relocation support + address-space teardown prerequisites done & verified

**Scope, stated precisely per direct project direction (2026-08):**
every ordinary Windows desktop/PC application (`.exe`/`.msi`
userspace software) must run. The *only* exclusion is software that
genuinely requires operating at Windows' own kernel level -- kernel-mode
drivers, WDM/WDF-based anti-cheat or DRM, low-level filter drivers, and
similarly privileged components that would require Nyrqis to host
actual Windows kernel-mode driver execution. This matches ADR-0005
("Windows compatibility via translation layer, not full emulation,"
**confirmed Accepted**, re-verified against `NPC-004 Specification
Index` v1.22.0) rather than requiring a new architectural decision.

**Action item attempted this session, still genuinely open:** checking
NPS-007's full current text against this exact kernel-mode-exclusion
wording. `NPC-004` confirms NPS-007 itself is `Accepted` (subsystem:
`runtime`), but this session's tooling could not retrieve the document's
full body (GitHub directory browsing is blocked for the fetch tool used
here, and web search did not surface the specific file path under
`docs/reference/`). This is a genuine gap, not a checked-and-passed
item -- do not treat NPS-007 as confirmed to state this boundary until
someone (human or a session with working repo browse access) actually
reads it.

**Also newly relevant, per the Phase 6 correction above:** non-native-
architecture Windows apps (e.g. x86 Windows software on an ARM Nyrqis
device) would depend on ADR-0015 (shared dynamic binary translation),
which is `Proposed`, not `Accepted`. Native-architecture Windows app
support does not depend on ADR-0015 and is unaffected by this.

**Concrete kernel-level prerequisite identified and built this
session, not previously tracked:** nothing in Phases 1-4 ran at CPL 3
(ring 3/user mode) -- every experimental task executed at kernel
privilege. Neither compatibility layer can exist until *something*
runs application code at CPL 3, so this was built and verified as a
`crate::experimental` proof of concept (same pre-normative treatment
as Phases 3-4; this is a kernel-architecture prerequisite, not
NPS-007/008 conformance):

- User code/data GDT segments (DPL 3) and a TSS RSP0 (ring-0 stack for
  the automatic privilege-level switch on trap entry) -- `src/gdt.rs`
- An `int 0x80` IDT gate explicitly granted `DPL=Ring3` (gates default
  to DPL 0; a ring-3 `int` to a DPL-0 gate is itself a fault) -- `src/idt.rs`
- A hand-written `iretq`-based ring-3 transition (`src/experimental/usermode.rs`)
- Pages explicitly mapped `USER_ACCESSIBLE` via the Phase 2 dynamic
  mapper, holding a tiny hand-assembled machine-code program (no
  ELF/PE loader exists yet -- out of scope for this proof)

**Two-sided verification, not just a happy-path pass:**

```
  experimental usermode: entering ring 3 (this is kernel_main's last action -- see idt::syscall_handler for the finale)
  experimental usermode self-test: int 0x80 received from CPL 3 (ring 3) -- OK
nyrqis kernel: phase 1+2+3+4+ring3-poc complete
PASS: kernel reached BootPhase::Ready and exited cleanly
```

And the negative control -- rebuilding with `USER_ACCESSIBLE` removed
from the page flags, to confirm the flag is doing real work rather
than being decorative in a setup where privilege checking wasn't
actually being enforced:

```
EXCEPTION: PAGE FAULT
accessed address: VirtAddr(0x20000000000)
error code: PROTECTION_VIOLATION | USER_MODE
```

Immediate, correctly diagnosed fault -- confirming the permission
model is real, not assumed. (Reverted immediately after; not left in
the shipped code.)

**A real design correction made mid-implementation, worth recording:**
the original plan ran the ring-3 transition as an `experimental::scheduler`
task. That doesn't work -- `iretq` is a one-way privilege transfer, and
a trap handler resumes wherever it interrupted (ring 3), never back to
whichever kernel task called `enter()`. A scheduler task built this way
could never `yield_task()` back, silently breaking the round-robin
invariant every other `experimental` demo depends on. Fixed by making
ring-3 entry `kernel_main`'s own final action, with `idt::syscall_handler`
-- the only kernel code guaranteed to run afterward -- doing the final
reporting and halt/exit instead of returning via `iretq`.

**Extended further this session: a real minimal ELF64 loader, not
hand-assembled test bytes.** The initial ring-3 proof above used a
6-byte hand-assembled program hardcoded to match wherever the loader
expected it -- that proved the CPU privilege mechanics work, but not
that this kernel can load and run an actual compiled executable, which
is the thing Phase 5/6 will actually need. Replaced with:

- `src/experimental/elf.rs` -- parses ELF64 file/program headers
  (`e_entry`, `PT_LOAD` segments' `p_vaddr`/`p_filesz`/`p_memsz`/`p_flags`),
  bounds-checked against malformed input with the same rigor as
  `multiboot.rs`. Maps each segment at the address *the file specifies*
  (not a hardcoded address), zero-fills the full mapped range first
  (so `p_memsz > p_filesz`, i.e. an implicit `.bss`, is handled
  correctly rather than leaving stale physical-frame content), then
  copies the file's actual bytes on top
- `userprog/prog.s` + `userprog/linker.ld` -- a real, separately-built
  ELF64 executable (same `as`/`ld` toolchain as the kernel itself, a
  completely independent build), embedded into the kernel via
  `include_bytes!`. Its entry point writes a magic value
  (`0xC0FFEE`) to the already-mapped user stack page, then traps via
  `int 0x80` -- so the kernel can verify the *file's own code* actually
  ran, not merely that some code reached the syscall gate

**Real correctness proof, verified by a clean from-scratch QEMU boot,**
checking three independent things at once -- CPU privilege level,
entry point address genuinely read from the file (not assumed), and a
value only the loaded code itself could have computed:

```
  experimental elf: userprog.elf parsed and mapped, entry point 0x20000000000 (read from the file, not assumed)
  experimental usermode: entering ring 3 (this is kernel_main's last action -- see idt::syscall_handler for the finale)
  experimental usermode self-test: int 0x80 received from CPL 3 (ring 3), magic value 0xc0ffee matches -- ELF entry point genuinely executed -- OK
nyrqis kernel: phase 1+2+3+4+ring3-elf-poc complete
PASS: kernel reached BootPhase::Ready and exited cleanly
```

**Negative control on the magic-value check itself** (temporary,
reverted immediately after -- not shipped): set the kernel's expected
value to a deliberately wrong constant and confirmed the self-test
correctly reports failure rather than always passing:

```
  experimental usermode self-test: FAILED -- cpl=3 (expected 3), magic=0xc0ffee (expected 0xdeadbeef)
```

**Still not implemented, and explicitly out of scope for this proof:**
relocations, dynamic linking, `ET_DYN`/PIE executables, symbol tables,
and NX enforcement (EFER.NXE is not yet enabled -- every mapped page
is executable regardless of the file's `PF_X` flag, a carried-over
Phase 2 simplification). A real Windows PE loader or Android ELF/DEX
loader (Phase 5/6 proper) needs substantially more than this, but
needs this exact category of work underneath it.

**Extended further this session: real per-process address-space
isolation, replacing the single shared-page-table design above.**
Everything up to this point ran ring-3 code in the kernel's own page
tables -- no isolation between processes, or between a process and the
kernel, existed. Built:

- `src/experimental/address_space.rs` -- allocates a fresh top-level
  page table (PML4) per process, sharing exactly the entries that must
  stay shared (the kernel's identity-mapped low memory and the kernel
  heap -- both needed so interrupts, `serial_println!`, and `alloc::*`
  keep working regardless of which process's address space is active)
  while giving each process private, unshared entries for its own code
  and stack
- `src/experimental/isolation_demo.rs` -- orchestrates a genuine
  two-process proof: build address space A, run process A (writes
  `0xC0FFEE`), trap; build address space B *while A is inactive but
  preserved*, run process B (writes `0xBBBBBBBB`) **at the identical
  virtual address A used**, trap; switch back to A's address space and
  re-read that same virtual address
- A real refactor needed to make this possible:
  `BumpFrameAllocator` used to borrow `MultibootInfo` with a lifetime
  tied to a `kernel_main` local, which doesn't work once the frame
  allocator needs to be reachable from inside an interrupt handler
  across multiple trap-driven stages. Fixed by making `MultibootInfo`
  `Copy` and having `BumpFrameAllocator` own it by value -- removing
  the lifetime parameter entirely, a small, real, worthwhile
  simplification that also makes the frame allocator easier to reason
  about generally, not just for this demo

**The actual isolation proof, and why it's a real one:** two processes
use the *identical* virtual address (`0x20000000000`) for their code
and stack, backed by two provably distinct physical page-table chains
(different PML4 physical addresses, confirmed in the boot log). If
isolation were broken -- e.g. a bug that accidentally shared the
user-code page-table entries between A and B -- switching back to A
after B ran would read B's value instead of A's. It doesn't:

```
  experimental isolation: address space A built (pml4 0x19d000), program A loaded, entry 0x20000000000
  experimental isolation: trap from process A (cpl=3), read 0xc0ffee (expected 0xc0ffee) -- OK
  experimental isolation: address space B built (pml4 0x1a3000), program B loaded, entry 0x20000000000 -- switched active, A is now inactive but preserved
  experimental isolation: trap from process B (cpl=3), read 0xbbbbbbbb (expected 0xbbbbbbbb) -- OK
  experimental isolation: switched back to process A's address space, re-read 0xc0ffee (expected 0xc0ffee, unchanged since A ran) -- OK, ISOLATED
  experimental isolation self-test: two independent address spaces verified mutually isolated -- OK
nyrqis kernel: phase 1+2+3+4+ring3-elf-isolation-poc complete
PASS: kernel reached BootPhase::Ready and exited cleanly
```

**Negative control, with an honest surprise worth recording:** the
plan was to deliberately share the PML4 index covering the user-code
region between A and B (simulating the isolation bug this whole demo
exists to catch) and confirm the isolation check reports corruption.
Instead, it failed at a *different, arguably stronger* point: process
B's attempt to map its own code/stack pages into the now-shared
page-table region hit `Mapper::map_to`'s "page already mapped" error
(since A's mapping was already sitting there), and the kernel panicked
cleanly during setup rather than ever reaching a state with silently
corrupted data:

```
  experimental isolation: trap from process A (cpl=3), read 0xc0ffee (expected 0xc0ffee) -- OK
KERNEL PANIC: panicked at src/experimental/isolation_demo.rs:135:37:
failed to load program B: Elf(MappingFailed)
```

Recorded as-is rather than engineered to match the original prediction
-- the finding is arguably better than expected (broken isolation
fails loudly at construction time, not silently at data-corruption
time), but the honest thing is to report what actually happened, not
what was anticipated.

**A real design correction, per the same recurring pattern this
session:** `experimental::isolation_demo::on_syscall_trap`'s
`WaitingForA` branch originally risked continuing past a failure
silently; written instead so `report_failure_and_exit` (a `-> !`
function) makes the "stop here on failure" behavior structurally
guaranteed by the type system, not just by careful control flow.

**Extended once more this session: address-space switching integrated
into the ordinary cooperative scheduler, not just a hand-orchestrated
one-shot demo.** `experimental::isolation_demo`'s A -> B -> A sequence
proved isolation across exactly one switch away and back. What a real
scheduler actually needs is that property holding under *repeated*
switching -- so `experimental::scheduler::TaskControlBlock` gained a
`cr3` field, and `switch_to`'s hand-written asm now reloads `CR3` as
part of every task switch (`spawn_with_cr3` for tasks with their own
private address space; ordinary `spawn` for tasks that keep sharing
whatever's currently active, unchanged from Phase 3).

**Proof:** `experimental::isolated_tasks::run` extends the existing
3-task round-robin (main, A, B) to 5 by adding tasks C and D, each
with its own private address space, driven through **50 real CR3
switches each** -- not one, fifty -- while A and B's original IPC demo
continues running correctly in the same round-robin the whole time:

```
  experimental isolated-tasks: 5 total tasks in round-robin (main, A, B, C[own cr3 0x19f000], D[own cr3 0x1a4000])
    ... (A/B's IPC demo continues interleaving correctly throughout) ...
  experimental isolated-tasks self-test: C 50 ok / 0 failed, D 50 ok / 0 failed (across 50 switches each, repeated CR3 reload)
```

**Negative control, another honest surprise:** disabling the `mov cr3,
rdx` instruction (simulating "the switch forgets to reload the address
space") was expected to produce silently wrong data. Instead, task C's
very first write to its own supposedly-private address immediately
page-faulted:

```
EXCEPTION: PAGE FAULT
accessed address: VirtAddr(0x30000000000)
error code: CAUSED_BY_WRITE
```

Because without the reload, whatever space was actually still active
(main/A/B's shared one) never had that address mapped at all -- so
the fault is immediate and unambiguous, not a subtle data-corruption
bug that might pass undetected for a while. Arguably a *more*
convincing proof than the corruption I was originally testing for: it
shows the `mov cr3, rdx` instruction is exactly what makes C and D's
isolated memory reachable in the first place, not a redundant
safety net protecting against a failure mode that wouldn't otherwise
occur.

**A real latent bug identified and fixed proactively, before it caused
a failure.** `experimental::isolation_demo`'s processes A and B were
both using the *same* static ring-0 stack (`gdt::PRIVILEGE_STACK`,
set up once at boot) as their RSP0 -- the stack the CPU switches to on
a ring-3->ring-0 trap. This hadn't caused a visible bug because neither
process was ever *resumed* after being suspended (each trap led
straight to reporting-and-halting, or to building and switching to the
next process). But it's the wrong design, and would corrupt a
suspended process's saved trap state the moment two processes' RSP0
usage actually overlapped in time -- exactly the scenario needed for
genuine ring-3 task scheduling (see "still missing" below). Fixed
before it became a hard-to-diagnose bug later: `AddressSpace` now owns
its own private kernel stack, and `activate()` updates `TSS.RSP0` to
it alongside `CR3`. Verified with a full regression run across every
existing demo (scheduler, IPC, isolated-tasks, isolation) -- all still
pass, confirming the fix didn't disturb anything it wasn't meant to.

**Still explicitly missing, now precisely identified as the next real
integration point:** ring-3 execution driven *through* the cooperative
scheduler (rather than isolation_demo's hand-orchestration) needs a
full general-purpose-register save/restore on every trap -- the
`extern "x86-interrupt"` handlers used everywhere else in this kernel
don't expose GPRs, only the architectural interrupt frame, which is
enough for reporting results but not enough to suspend and later
resume a ring-3 task's complete execution state. That needs a
hand-written naked trap handler in the same style as `switch_to`, just
substantially larger (all GPRs, not six callee-saved registers). Not
attempted this session -- flagged honestly as real, sizable remaining
work rather than attempted partially.

---

**Built and verified in a later session: exactly that missing piece.**
`src/experimental/ring3_scheduler.rs` -- a full general-purpose-
register save/restore trap handler, installed at its own dedicated
vector (`0x81`, distinct from `0x80`) so it can't be confused with
`usermode`/`isolation_demo`'s one-shot report-and-halt traps. Two real
ring-3 tasks ("E" and "F"), each with their own private address space
(reusing `experimental::address_space`), suspended and resumed via
genuine `int 0x81` traps -- not reported-and-abandoned, actually
resumed with complete register state intact, repeatedly.

**How it works, briefly:** the naked handler pushes all 15
general-purpose registers on top of the hardware-pushed interrupt
frame (RIP/CS/RFLAGS/RSP/SS), calls into a normal Rust dispatcher
(passing a pointer to the whole 160-byte blob), which records the
outgoing task's context pointer, switches `CR3`+`RSP0` (via
`AddressSpace::activate`) to the next task, and returns *that* task's
saved context pointer -- which the asm then restores from and
`iretq`s into. A task that has never run before (task F) gets a
hand-constructed initial blob in the identical layout, so the restore
path is uniform whether it's resuming real saved state or "resuming"
into a first run.

**The proof, and why it's a strong one:** each task's user-mode program
(`userprog/prog_yield.s`, the same binary loaded into each of the three
address spaces) keeps a counter purely in register `r12` -- not memory --
incrementing it once per activation, writing it to private memory, then
yielding. If the register save/restore were subtly wrong, this counter
would reset, corrupt, or diverge between activations; it doesn't, across
**21 real trap-based yields** (7 per task, driven by genuine
round-robin suspend/resume, not simulated):

```
  experimental ring3-scheduler: 3 tasks built (E entry 0x50000000000; F, G pre-loaded with initial trap blobs), target 21 total yields (7 per task)
  experimental ring3-scheduler self-test: task E counter=7 (expected 7) -- OK
  experimental ring3-scheduler self-test: task F counter=7 (expected 7) -- OK
  experimental ring3-scheduler self-test: task G counter=7 (expected 7) -- OK
  experimental ring3-scheduler self-test: 21 real trap-based yields processed, all 3 tasks' full register + address-space state correctly suspended and resumed -- OK
```

Chains into `isolation_demo` afterward with the same frame allocator
(rather than exiting directly), keeping the whole growing demo chain
as one unified, fully verified boot sequence -- confirmed by the full
clean-build transcript reaching all the way through `isolation_demo`'s
own self-test and final `PASS`.

**Negative control, this time matching the prediction exactly** (unlike
several earlier ones this session, which failed louder/earlier than
expected): deliberately swapping the push order of two adjacent
registers (`r11`/`r12`) without correspondingly swapping the pop order
-- corrupting exactly the register the test relies on -- produced
genuinely wrong, non-garbage-looking-by-accident values (`E
counter=1115125`, `F counter=5`) and a correctly reported `MISMATCH`
on both, while the rest of the boot sequence (including
`isolation_demo`'s independent self-test afterward) continued and
still passed correctly -- confirming the failure was real, detected,
and contained rather than corrupting anything beyond the specific
mechanism being tested.

**First real boot attempt, zero bugs** -- notable given this is by a
wide margin the most intricate hand-written assembly in this kernel:
a 20-`u64` fixed-layout blob shared between hand-written push/pop
sequences and a separately-written Rust blob constructor, a raw
(non-`x86-interrupt`-ABI) IDT entry installed via `set_handler_addr`,
and a `call` from naked asm into normal Rust and back. Attributed
directly to the same discipline used throughout this session: working
out the exact memory layout on paper (documented in the module's own
doc comment) before writing a single line of asm, rather than writing
first and debugging the layout into correctness afterward.

**What's genuinely still missing, honestly enumerated:** address-space
teardown when a task "exits" (none of these tasks ever do -- they spin
forever after their last yield in this demo's bounded run), more than
two tasks in the ring-3 round-robin, preemptive (timer-driven) rather
than only cooperative ring-3 yielding, and of course everything Phase
5/6 proper still needs on top of this -- a PE/DEX loader, relocations,
dynamic linking, and the actual Windows/Android API surface. This
closes the specific "ring-3 + scheduler integration" gap identified at
the end of the previous session; it does not shrink the much larger
gap between kernel prerequisites and an actual compatibility layer.

---

**Built and verified in a later session: NX (no-execute) enforcement,
closing a gap flagged repeatedly since Phase 2** ("every mapped page is
executable regardless of a segment's `PF_X` flag" appears in the
"still missing" list of nearly every prior entry in this file). Real
W^X hardening, not a partial gesture:

- `memory::paging::enable_nx()` sets `EFER.NXE` -- without this MSR
  bit, the page-table `NO_EXECUTE` flag is architecturally reserved
  and silently ignored regardless of what's set on any entry, so this
  is the real prerequisite, not a formality
- `experimental::elf::load` now actually honors each `PT_LOAD`
  segment's `PF_X` flag: segments without it get `NO_EXECUTE`, so a
  loaded program's data is genuinely non-executable, not just
  logically intended to be
- Every stack and private data mapping across every experimental demo
  (`usermode`'s user stacks, `ring3_scheduler`'s per-task data/stack
  pages, `isolated_tasks`' private pages, the kernel heap itself) now
  carries `NO_EXECUTE` too -- none of them are ever meant to hold
  executable code, and now the CPU enforces that rather than it being
  merely true by convention

**Full regression, since this touches memory flags every existing demo
depends on:** every prior proof in this file -- scheduler, IPC,
isolated-tasks' 50 switches, ring3-scheduler's 20 real trap-based
yields, isolation_demo -- still passes unchanged with NX enabled,
confirming no legitimate code path was accidentally marked
non-executable.

**A genuine positive control, not just a negative one:** rather than
only confirming nothing broke, a temporary addition (reverted
immediately after, per this session's established pattern) deliberately
wrote a single `ret` instruction byte into a heap allocation and
attempted to call it as a function -- an intentional violation of the
new NX policy:

```
  NX positive-control: attempting to execute from heap memory at 0x0000010000000000 (expect an immediate instruction-fetch page fault)
EXCEPTION: PAGE FAULT
accessed address: VirtAddr(0x10000000000)
error code: PROTECTION_VIOLATION | INSTRUCTION_FETCH
```

Immediate, correctly diagnosed fault -- `INSTRUCTION_FETCH` specifically,
confirming the CPU refused the fetch itself, not some unrelated access
check. This is the first *positive* control this session (proving a
protection mechanism actively blocks a real violation) rather than a
negative control (proving something *stops* working when a mechanism
is removed) -- a meaningfully different and arguably stronger kind of
evidence, since it demonstrates the mechanism doing its job under a
genuine attempt to defeat it, not just its absence being consequential.

---

**Built and verified in a later session: real position-independent
executable (`ET_DYN`) loading with actual relocation processing --
closing another gap flagged since Phase 4** ("relocations, dynamic
linking, `ET_DYN`/PIE executable support... not implemented" appears
in every prior entry's "still missing" list).

**Verified the toolchain first, before writing any Rust:** confirmed
`ld -pie --no-dynamic-linker` on this system's GNU binutils actually
produces a self-contained "static-pie" binary (no interpreter, no
external symbol dependencies) with exactly one `R_X86_64_RELATIVE`
relocation for a hand-written test program's absolute data pointer,
via `readelf -r`, before designing anything to process it.

**`experimental::elf::load` extended, not replaced:** a new
`load_bias` parameter (required `0` for `ET_EXEC` -- a fixed-address
binary would be wrong at any other address, enforced as an error, not
just documented) lets `ET_DYN` binaries be mapped at a base address
the loader chooses. A new pass over the `PT_DYNAMIC` segment finds the
`DT_RELA`/`DT_RELASZ` relocation table and applies every
`R_X86_64_RELATIVE` entry (`*(base + r_offset) = base + r_addend`) --
the only relocation type a self-relocating, no-external-symbols binary
needs; anything else is correctly rejected rather than silently
skipped, since this loader has no real dynamic linking to satisfy it.

**The proof:** `userprog/prog_pie.s`, a genuine `ET_DYN` binary whose
`.data` holds an absolute pointer to another symbol in the same file --
a real relocation target, not a contrived one, confirmed with
`readelf` before any kernel code touched it. Loaded at a base address
(`0x60000000000`) nowhere close to what the file itself specifies,
dereferencing that pointer correctly retrieves the expected value:

```
  experimental pie-demo: ET_DYN loaded at base 0x60000000000, entry 0x60000001000 (bias correctly applied by the loader, not baked into the file)
  experimental pie-demo self-test: relocated pointer dereferenced to 0xfeedface12345678 (expected 0xfeedface12345678) -- OK, relocation correctly applied
```

**A real bug found and fixed on the first boot attempt** -- the pattern
holding steady across every increment this session: the program faulted
immediately with `CAUSED_BY_WRITE | USER_MODE` at its private data
address. Root cause, found quickly because the fault report is precise
and this kernel's page-table-based isolation makes "which mapping is
missing" easy to localize: a plain omission -- the demo mapped a user
stack but never mapped the private data page the program writes its
result to. One straightforward fix, immediately verified clean.

**Negative control, another loud-not-silent failure, consistent with
this session's recurring finding:** temporarily omitting the load bias
from the relocation's written value (simulating a loader that parses
relocations but applies them incorrectly) didn't produce a
plausible-looking wrong value -- it made the pointer resolve to the raw
file-relative address (`0x3000`), which isn't mapped anywhere in the
real, biased address space, so it faulted immediately rather than
silently reading garbage from wherever `0x3000` happened to be:

```
EXCEPTION: PAGE FAULT
accessed address: VirtAddr(0x3000)
error code: PROTECTION_VIOLATION | USER_MODE
```

**Still genuinely out of scope:** symbol-resolved relocation types
(`R_X86_64_GLOB_DAT`, `R_X86_64_JUMP_SLOT`, ...), which need real
dynamic linking (symbol tables, string tables, name resolution against
other loaded objects) that this loader doesn't attempt and correctly
refuses rather than mishandles. A `PT_GNU_RELRO` "make read-only again
after relocating" step is also not implemented -- a relocatable
segment this loader maps writable stays writable, a documented
simplification, not a silent gap.

---

**Built and verified in a later session: real address-space teardown,
closing the gap every prior entry in this section flagged** ("every
experimental address space leaks forever via `mem::forget`"). Not a
partial gesture -- an actual free-list-backed physical frame allocator
plus full page-table-tree walking and freeing:

- `memory::frame_allocator::BumpFrameAllocator` gained a real
  free-list (a `Vec<PhysFrame>`, backed by the kernel heap's own
  allocator, which already supported real deallocation -- only the
  *physical frame* allocator didn't). `FrameDeallocator` implemented
  so frames come back through the same object that handed them out.
  Freed frames are reused LIFO before the bump pointer ever advances
  further
- `experimental::address_space::AddressSpace::destroy` walks a
  space's *entire private* page-table hierarchy (PDPT -> PD -> PT ->
  data frames), freeing every frame it owns, while explicitly skipping
  the shared kernel/heap PML4 entries -- freeing those would corrupt
  every other live process, including the kernel itself

**The proof, and why the reuse check is unambiguous, not just
plausible:** build a space, use it (write and read back a real value
through it), destroy it, then immediately build a second space --
`destroy` frees a space's own PML4 frame *last*, so the very next
frame allocation (that second space's own PML4) should pop exactly
that frame back off the free list. Not "some frame got reused
somewhere" -- the *exact* physical address:

```
  experimental teardown-demo: space X used (wrote+read 0xdeadc0dedeadc0de -- OK), 5 frames reclaimed on destroy (82 -> 77 in-use, baseline 77) -- fully reclaimed
  experimental teardown-demo: space Y's PML4 (0x1b6000) == space X's just-freed PML4 (0x1b6000) -- OK, frame genuinely reused
```

**First real boot attempt, zero bugs** -- for recursive page-table-tree
freeing across three levels, coordinated with a brand-new
free-list allocator, worked correctly the first time it ran.

**Negative control that matched the prediction exactly:** deliberately
skipping the PML4 frame's own deallocation (simulating an incomplete
`destroy` that frees a process's page-table subtree but forgets the
top-level table itself) produced precisely the expected result -- one
fewer frame reclaimed than baseline (`4` instead of `5`, the exact
frame withheld), correctly flagged `LEAK`, and the reuse check
correctly reporting the freshly-built space's PML4 did *not* land on
the leaked frame:

```
  experimental teardown-demo: space X used (wrote+read 0xdeadc0dedeadc0de -- OK), 4 frames reclaimed on destroy (82 -> 78 in-use, baseline 77) -- LEAK
  experimental teardown-demo: space Y's PML4 (0x1b8000) != space X's just-freed PML4 (0x1b6000) -- not reused
KERNEL PANIC: panicked at src/lib.rs:203:5:
address-space teardown did not fully reclaim its frames, or the reclaimed frame wasn't reused
```

**Still genuinely open, stated plainly:** this frees frames, not the
`Vec<u8>` kernel-stack allocation directly -- that already worked via
the heap allocator's own real `dealloc`, so it needed no new machinery,
but is worth naming so "teardown" isn't overclaimed as covering more
than physical frames. TLB invalidation on unmap -- once named open
here -- is now implemented: `memory::paging::unmap_page` emits `invlpg`
via the mapper's flush, proven by `memory::tlb_demo` (map -> unmap ->
remap the same VA, reading back the new frame). And teardown of a space
that was ever the *active* one for a ring-3 trap -- the documented
sharp edge in `destroy`'s own safety comment -- is also now closed:
`ring3_scheduler`'s finale destroys task F's address space after F has
yielded from ring 3 seven times and is suspended for good, verifying
10 frames come back (`pml4 0x297000 ... 10 frames reclaimed (305 ->
295 in use) -- OK`).

---

**Built and verified in a later session: the first real Phase 5
capability, not another kernel-internals prerequisite.** Everything
above built the *mechanics* Phase 5 needs (ring 3, per-process address
spaces, a loader pattern proven against ELF); this is the first module
that loads Windows' own executable format.

**Verified the toolchain first, again, before writing any Rust:**
confirmed `ld -m i386pep --subsystem native -e _start` on this
system's GNU binutils -- no MSVC, no MinGW runtime, nothing
Windows-specific at all -- produces a genuine PE32+ file. `file`
reports "PE32+ executable (native) x86-64"; `objdump -p` confirms a
real `IMAGE_OPTIONAL_HEADER64` (Magic `0x20b`) with a real `ImageBase`
and, critically, **zero base relocations** -- meaning the same
fixed-address-first scoping that worked for ELF's `ET_EXEC` applies
directly.

**`src/experimental/pe.rs`** parses DOS/PE/COFF/Optional headers and
the section table with the same bounds-checking rigor as
`experimental::elf` and `crate::multiboot`, maps each section with
real permissions from its `Characteristics` flags (W^X enforced via
`NO_EXECUTE`, same as ELF), and -- a real safety property, not just
documentation -- **checks the Base Relocation data directory and
rejects outright** any PE with nonzero relocations, rather than
silently loading it incorrectly at its fixed address anyway.

**The proof:** `userprog/prog_pe.s`, built entirely with GNU
`as`/`ld`, no Windows toolchain anywhere, loaded at its own
`ImageBase` with no relocation:

```
  experimental pe-demo: PE32+ loaded at its own ImageBase (no relocation), entry 0x140001000
  experimental pe-demo self-test: PE entry point wrote 0xfeedc0defeedc0de (expected 0xfeedc0defeedc0de) -- OK, a genuine Windows-format (PE32+) executable's own code ran in ring 3
```

**First real boot attempt, zero bugs** -- notable given this is an
entirely new file format this kernel had never parsed before, built on
infrastructure (ring 3, per-process address spaces, W^X, the loader
pattern) that was itself all built this session.

**Negative control, matching the prediction exactly:** deliberately
setting the kernel's expected value wrong produced a correctly
reported `MISMATCH`, with the rest of the demo chain (including
`isolation_demo` afterward) unaffected -- same pattern as the PIE
relocation check's own negative control.

**What this genuinely proves, and what it doesn't:** a real,
Windows-format executable's own machine code can be loaded and
executed by this kernel. It does **not** mean Windows applications can
run -- there is no import resolution (a binary calling into
`kernel32.dll` or any other DLL does nothing here), no base
relocation processing (ASLR-enabled binaries, which is most real-world
Windows software, are rejected outright by design), no Win32 API
surface of any kind. This is the loader-mechanics equivalent of
`experimental::elf`'s first `ET_EXEC` proof -- a real, necessary first
step, not a compatibility layer.

**A note on continuity:** this specific increment was built twice.
The first attempt was fully completed and verified (including its
negative control) in one session, but the sandbox reset before the
resulting files were packaged for delivery -- no engineering doubt was
introduced by this, since the work was re-applied from the verified
source captured in that session's own transcript and re-verified
byte-for-byte identical (same PML4 addresses, same entry point, same
transcript) against the pre-reset run. Recorded here because silently
omitting it would misrepresent how this increment actually got built.

## Phase 6 -- Android compatibility 🚧, first real capability landed (minimal DEX header/format parser)

**Scope, stated precisely:** full application compatibility -- an
installed `.apk`'s app is expected to run, full stop, no carve-out
(unlike Phase 5, there is no kernel-level exclusion category for
Android, since Android apps run in userspace against the Android
framework rather than needing Android kernel driver access the way
some Windows software needs NT kernel driver access). NPS-008 (Android
Compatibility Runtime) is Accepted and ADR-0008 (AOSP-based container
runtime) is Accepted, but **correction from an earlier version of this
document:** ADR-0015 (shared dynamic binary translation for ARM/x86,
which both NPS-007 and NPS-008 lean on for cross-architecture app
support) is `Proposed`, not `Accepted` -- re-verified directly against
`NPC-004 Specification Index` v1.22.0. This matters concretely: the
container-runtime-level spec (NPS-008) being Accepted does not by
itself settle how ARM-on-x86 (or x86-on-ARM) binary translation works,
which both this phase and Phase 5 depend on for non-native-architecture
apps. That ADR reaching Accepted is a real prerequisite for this
phase's non-native-arch scope, not implementation detail to fill in
along the way.

---

**Built and verified in a later session: the first real Phase 6
capability, mirroring Phase 5's PE loader in both scope and rigor.**

**A real, important scope distinction made explicit up front, not
discovered partway through:** Android's *native* code (anything
compiled via the NDK from C/C++/Rust) already uses the ELF format
`experimental::elf` handles -- no separate native loader was needed.
What's actually Android-specific is DEX: the bytecode container format
for an app's Java/Kotlin code. This module parses that *container
format*. **It does not execute any bytecode** -- that needs a real
interpreter or AOT compiler (what ART actually does), an undertaking
comparable in scope to writing a JVM, and explicitly out of scope.
Stated this precisely in `src/experimental/dex.rs`'s own module doc,
not left implicit.

**Toolchain-verification discipline extended to a format with no
existing toolchain to test against:** unlike ELF/PE, there's no
`as`/`ld` equivalent for hand-assembling DEX. Instead, a real,
structurally-valid, checksum-and-SHA1-signature-correct minimal DEX
file was constructed with Python's own `struct`/`hashlib`/`zlib`
(mirroring the exact spec layout: 112-byte fixed header, a
spec-required `map_list` that a well-formed DEX must use to describe
itself) and independently re-parsed in a second, separate Python pass
before any Rust touched it -- the same "verify the artifact
independently before writing kernel code against it" discipline as
the PE/PIE toolchain checks, adapted to a format with no compiler to
lean on.

**`src/experimental/dex.rs`** parses the header and `map_list` with
the same bounds-checking rigor as every other `experimental` parser,
extracts section counts, and includes a genuine structural check, not
just a presence check: it walks the `map_list`'s entries confirming
one of them is a `TYPE_MAP_LIST` entry whose recorded file offset
matches the map's own location -- a DEX file that claims entries but
never actually describes its own map table is rejected as malformed,
not silently accepted.

**A nice property of this being pure data validation, not privileged
CPU state:** unlike every ring-3/paging demo this session, DEX parsing
needs no address space, no privilege transition, no trap handler --
so both the positive proof and two negative checks run as ordinary
function calls in the same boot, returning normally. **The negative
checks are permanent, shipped code, not a temporary rebuild-and-revert
test** -- they run on every future boot too, not just once during
development:

```
  experimental dex-demo: parsed a real DEX file (file_size=128, map_entry_count=1, all section counts zero as constructed) -- OK
  experimental dex-demo: corrupted magic -- parse result Some(BadMagic), correctly rejected: true
  experimental dex-demo: corrupted map self-reference -- parse result Some(NoMapListEntry), correctly rejected: true
  experimental dex-demo self-test: valid parse OK, bad-magic rejection OK, map-self-reference rejection OK -- OK
```

**First real boot attempt, zero bugs** -- for an entirely new file
format, hand-constructed with no toolchain to verify against beyond a
second independent Python parse.

**What this does and doesn't mean, stated as plainly as PE's own
equivalent note:** a real DEX file's container format can be parsed
and validated -- not that Android apps can run. No bytecode
interpretation of any kind, no ART/Dalvik semantics, no APK (ZIP)
container unwrapping (this parser handles a standalone `.dex` file;
extracting `classes.dex` from inside a real `.apk` needs ZIP parsing,
not attempted here). The `checksum`/`signature` fields, once parsed
but unverified, are now cryptographically verified in-kernel:
`experimental::crypto` provides Adler-32 and SHA-1, and
`experimental::dex::verify` checks both -- with published-vector
known-answer tests plus a corrupted-checksum negative check.

**A note on continuity, same as Phase 5's PE loader:** this increment
was built, fully verified (including both permanent negative checks),
lost to a sandbox reset before packaging, and rebuilt from the
verified source in the session's own transcript -- re-verified as
producing an identical transcript (same file_size, same checksum, same
every reported value) against the pre-reset run. A second, independent
reset happened during the *documentation* pass for this very phase;
recovered from a surviving build artifact in `/mnt/user-data/outputs/`
rather than needing to ask again. Recorded here, not smoothed over.

## Phase 7 -- GPU / graphics kernel path ⬜

NPS-001 §3 names the GPU command submission path as the one deliberate
exception to "everything non-essential lives in user space" for
performance reasons, with a required fast-path command-buffer
validation and submission timeout/preemption (per the STRIDE Phase 2
amendment). `FIND-KERNEL-002` (GPU memory not cleared between
containers) is still open per the security threat model and must be
resolved as part of this phase, not deferred again.

## Phase 8 -- Storage integration ⬜

Wires real storage into the kernel/NyHAL contract. NyVault (envelope
encryption, quotas, snapshots -- already implemented as a userspace
NyHAL backend service in `source/nyhal-linux-backend/`) is the
functional precedent; this phase is that contract's native-kernel
implementation, not a redesign.

## Phase 9 -- Gaming subsystem / multi-device targets ⬜

`.nygi` disk images + overlay (NPS-006, Accepted), controller/input
(NPS-012, Accepted, VR explicitly deferred), the Emulator Hub
(NPS-014, Accepted). Also where non-desktop form factors (handhelds,
consoles, tablets, phones) become real targets rather than aspirational
README language.

## Phase 10 -- Secure Boot / UEFI ⬜, blocked on ADR-0014 remaining Proposed

ADR-0014 (UEFI Secure Boot with user-enrollable keys) is still
`Proposed`, pending Architecture Group review per NPC-004. Phase 1's
BIOS/GRUB2/Multiboot2 boot path is intentionally decoupled from this --
it neither assumes nor blocks UEFI Secure Boot landing later. This
phase starts only once ADR-0014 is Accepted.
