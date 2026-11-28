# Nyrqis Kernel -- Implementation Status

**Self-rating: Experimental. Phases 1-2 Complete and Verified against
NPS-001/017. Ten pre-normative Phase 5/6 kernel-level prerequisites
Complete and Verified (`src/experimental/mod.rs`'s banner -- none of
it is NPS-002/003 conformance). Two genuine first-capability
increments landed: a minimal PE32+ (Windows) loader, proven by
executing a real PE binary's own code in ring 3, and -- as of
2026-10-19 -- its import resolution: a real Windows-format DLL's own
export directory and a real client's kernel-resolved IAT, both
exercised in ring 3 by the client's own code (`pe_import_demo`),
and a minimal DEX (Android) format parser, proven against a real DEX
file whose Adler-32 checksum and SHA-1 signature are now verified
in-kernel, with both positive and permanent negative checks. On the
NPS-001-grounded side, several more documented gaps are now closed: a
growing set of real hardware IRQ lines with real handlers -- the RTC
periodic interrupt (IRQ8, `src/rtc.rs`), the PS/2 keyboard (IRQ1,
`src/keyboard.rs`, proven input-free by guest-driven scancode
injection), and the ATA/ATAPI IDE channels (IRQ14/IRQ15, `src/ata.rs`)
-- plus guard pages for stack-overflow detection (`memory::guard_demo`)
wired under *every* live kernel stack (ring-3 RSP0 and ring-0 scheduler
task stacks, all from `memory::kernel_stack`), verified every boot,
and timer-driven (preemptive) ring-3 task switching -- previously
cooperative-only -- proven by two ring-3 tasks that never trap being
round-robined purely by the timer interrupt (IRQ0). As of
2026-10-22, the whole demo chain also boots natively through the
Phase 10 UEFI stub (OVMF, GRUB nowhere in the path) with an identical
per-boot transcript to the Multiboot2/BIOS path, and
`experimental::elf`/`experimental::pe` follow an explicit
CR0.WP-independent loader write contract (map writable only for the
load/emit window, revoke to the file-requested flags) enforced by the
kernel's own boot-time CR0.WP setting -- see the 2026-10-22 increment
below for the boot-environment divergences this exposed.**

For a short, scannable summary of everything below, see `README.md`
(this directory) -- this file is the detailed, evidence-heavy log
behind that summary: every boot transcript, every bug found and
fixed, every negative control, in the order each was built.

This follows the same convention as
`source/nyhal-linux-backend/IMPLEMENTATION_STATUS.md`: a plain,
checkable statement of what works, evidenced by an actual build/boot
log, not a description of what's planned.

## What actually works, verified by a real QEMU boot (not simulated)

- Multiboot2-compliant boot via GRUB2 (`grub-mkrescue`) + QEMU
- 32-bit protected mode -> 64-bit long mode transition (CPUID checks,
  identity-mapped paging for the first 1GiB via 2MiB pages, EFER.LME,
  CR0.PG)
- A kernel-owned GDT with a TSS providing a dedicated double-fault IST
  stack
- An IDT with real handlers for divide-error, invalid-opcode, general
  protection fault, page fault, and double fault (verified these are
  wired correctly by the double-fault IST mechanism itself working --
  see "Bugs found and fixed" below, where an actual double-fault loop
  was diagnosed and resolved using this exact infrastructure)
- 16550 UART serial diagnostics (COM1), captured via QEMU `-serial
  stdio`
- An explicit, order-enforcing boot-phase state machine
  (`src/boot_phase.rs`) implementing NPS-001 §5's phase-transition
  order-validation requirement
- An automated pass/fail boot test (`tools/qemu_boot_test.sh`) using
  the isa-debug-exit QEMU device for a real process exit code, not
  eyeballed serial output
- A Multiboot2 memory-map parser (`src/multiboot.rs`), bounds-checked
  against malformed input, not just the happy path
- A physical frame allocator (`src/memory/frame_allocator.rs`) that
  correctly excludes the kernel image's own footprint and the low
  1MiB legacy region
- Dynamic page-table mapping (`src/memory/paging.rs`) via the `x86_64`
  crate's `Mapper`/`FrameAllocator` traits -- creates new page-table
  levels on demand, verified by mapping a heap far outside Phase 1's
  static identity range
- A working kernel heap (`src/memory/heap.rs`, `linked_list_allocator`)
  -- `alloc::vec::Vec`, `Box`, etc. are usable kernel-wide, verified by
  an actual allocate-and-sum self-test in `kernel_main`, not just a
  successful compile
- 8259 PIC remapping (`src/pic.rs`), off the CPU exception vector
  range, and a real hardware timer interrupt handler wired into the
  IDT with correct EOI signaling -- the first `sti` anywhere in this
  kernel, verified by counting 20 actual timer interrupts before
  proceeding (not just "the enable instruction didn't fault")
- A second real hardware IRQ line: the CMOS/RTC (MC146818) periodic
  interrupt on IRQ8 (`src/rtc.rs`). Chosen for this line because the
  RTC's programmable periodic interrupt fires on its own -- no input
  or injection needed to test it (the keyboard came later and is
  proven input-free by injection -- see below). This also exercises
  the slave-PIC
  cascade path the timer never did (IRQ8 arrives via IRQ2 on the
  master, both PICs must be unmasked, the handler must EOI both and
  read the RTC's register C to deassert the line) -- verified by
  observing real additional interrupts after a snapshot, not just a
  cumulative count that could predate the test
- Two more real hardware IRQ lines with real handlers: the PS/2
  keyboard (IRQ1, master PIC, `src/keyboard.rs`) and the ATA/ATAPI IDE
  channels (IRQ14 primary / IRQ15 secondary, slave PIC, `src/ata.rs`).
  The keyboard is proven *input-free* despite being an input device:
  the guest drives the 8042 controller to write a known scancode
  straight into its output buffer (command `0xD2`), which raises IRQ1
  exactly as a keystroke would -- no human and no QEMU monitor
  scripting. The IDE line is proven by issuing a real `IDENTIFY
  (PACKET) DEVICE` and observing the completion interrupt; it is
  explicitly conditional -- a machine with no IDE device reports that
  honestly and leaves the lines masked rather than pretending
- Guard-page stack-overflow detection (`memory::guard_demo`) -- a
  stack region mapped as usable pages with the page immediately below
  left deliberately unmapped, so an overflow faults deterministically
  instead of silently corrupting whatever sits below. Proven two ways
  every boot: structurally (`translate` confirms the stack pages are
  present and the guard page absent) and by enforcement (the demo
  actually writes into the guard page and the page-fault handler
  confirms the expected `CAUSED_BY_WRITE` fault and exits cleanly); if
  the write did *not* fault, the demo reports failure rather than
  silently passing. The same mechanism now backs every live kernel
  stack -- each ring-3 task's RSP0 stack (verified structurally every
  boot by `experimental::ring3_scheduler`) and each cooperative-
  scheduler (ring-0) task stack (verified by
  `experimental::scheduler::verify_guarded_stacks`), all from
  `memory::kernel_stack`
- A cooperative, round-robin, stack-switching scheduler proof of
  concept (`src/experimental/scheduler.rs`) -- a hand-written
  `#[naked]` context switch (save/restore SysV callee-saved registers
  + stack pointer), verified by running two tasks through exactly 6
  round trips each and asserting the resulting counts, not just "it
  didn't crash." **Explicitly pre-normative** -- see
  `src/experimental/mod.rs`'s banner: this is not NPS-002 (Process and
  Thread Model, still `Draft`) conformance, and is isolated in its own
  module specifically so that's visible in the source tree
- A real inter-task mailbox IPC proof of concept
  (`src/experimental/ipc.rs`) -- bounded per-task mailboxes with
  `send`/`try_receive` and real backpressure (a full mailbox is a
  reportable error, never a silent drop), wired into the scheduler
  demo: task A sends a `{sender, seq}` message to task B every
  activation, task B validates both fields. **Also explicitly
  pre-normative** -- not NPS-003 (IPC and Capability Passing, still
  `Draft`) conformance; no capability-scoped transfer validation or
  kernel-arbiter policy, just the delivery mechanism
- A ring-3 (user mode) transition proof of concept
  (`src/experimental/usermode.rs`) -- a concrete kernel-level
  prerequisite for Phase 5/6 (Windows/Android compatibility), since
  nothing built through Phase 4 ran at CPL 3. User GDT segments (DPL
  3), a TSS RSP0 stack, an `int 0x80` gate explicitly granted `DPL=Ring3`,
  and pages mapped `USER_ACCESSIBLE` via the Phase 2 dynamic mapper.
  Verified two ways: the real transition (CPU-reported CPL == 3 at the
  trap, not assumed) and a negative control (removing
  `USER_ACCESSIBLE` produces an immediate, correctly diagnosed
  `PROTECTION_VIOLATION | USER_MODE` page fault, confirming the
  permission model is actually enforced, not decorative)
- A minimal ELF64 loader (`src/experimental/elf.rs`) -- another
  concrete Phase 5/6 prerequisite. Parses real file/program headers
  (bounds-checked against malformed input, same rigor as
  `multiboot.rs`), maps `PT_LOAD` segments at the address the file
  itself specifies (not a hardcoded address), and zero-fills before
  copying file bytes so `p_memsz > p_filesz` (an implicit `.bss`) is
  handled correctly. Loads a real, separately-built ELF64 executable
  (`userprog/prog.s` + `userprog/linker.ld`, its own independent
  `as`/`ld` build, embedded via `include_bytes!`) whose entry point
  writes a magic value only its own code could produce, then traps.
  Verified two ways: the real load-and-run (entry point read from the
  file and genuinely jumped to; magic value `0xC0FFEE` matches) and a
  negative control (deliberately setting the kernel's expected value
  wrong produces a correctly reported `FAILED`, confirming the check
  has real teeth rather than always passing)
- Per-process address-space isolation (`src/experimental/address_space.rs`
  + `src/experimental/isolation_demo.rs`) -- the biggest remaining gap
  after the above: everything up to this point ran ring-3 code in the
  kernel's own page tables, no isolation between processes existed.
  Now: a fresh PML4 per process, sharing only the entries that must
  stay shared (kernel identity map + heap), private entries for each
  process's own code/stack. Proven with two real processes using the
  *identical* virtual address, backed by two distinct physical
  page-table chains (confirmed via differing PML4 physical addresses
  in the boot log): process A writes `0xC0FFEE` and traps, process B
  is built and run *while A is inactive but preserved* and writes
  `0xBBBBBBBB` at the same virtual address and traps, then the kernel
  switches back to A's address space and re-reads that address --
  still `0xC0FFEE`, proving genuine non-interference rather than
  coincidental non-corruption. Required a real refactor to make
  possible: `BumpFrameAllocator` used to borrow `MultibootInfo` with a
  lifetime tied to a `kernel_main` local, which doesn't work once the
  allocator needs to be reachable from inside an interrupt handler
  across multiple trap-driven stages -- fixed by making `MultibootInfo`
  `Copy` and having the allocator own it by value instead
- Address-space switching integrated into the ordinary cooperative
  scheduler (`experimental::scheduler`'s `TaskControlBlock` gained a
  `cr3` field; `switch_to` reloads `CR3` on every switch;
  `spawn_with_cr3` for tasks with a private address space) and proven
  under repeated switching, not just once
  (`src/experimental/isolated_tasks.rs`): two more tasks, C and D,
  each with their own private address space, driven through **50 real
  CR3 switches each** while the original A/B IPC demo keeps running
  correctly in the same round-robin throughout. Negative control
  (disabling the `mov cr3, rdx` reload): task C's very first write to
  its own "private" address immediately page-faulted
  (`CAUSED_BY_WRITE`) rather than silently corrupting data, since
  without the reload that address was never mapped in whichever space
  was actually still active -- proof the reload is what makes the
  isolation reachable at all, not a redundant safety net
- A latent bug found and fixed proactively: `experimental::isolation_demo`'s
  processes A and B were sharing one static ring-0 (RSP0) stack for
  trap entry. No visible failure yet -- neither process was ever
  *resumed* after suspension -- but it was the wrong design and would
  corrupt saved state the moment a suspended process's RSP0 usage
  overlapped with another's, which is exactly what real ring-3 task
  scheduling (see next item) needs. Fixed: `AddressSpace` now owns a
  private kernel stack, and `activate()` updates `TSS.RSP0` alongside
  `CR3`. Verified with a full regression run across every existing
  demo, not just the new behavior
- Real ring-3 task scheduling (`src/experimental/ring3_scheduler.rs`)
  -- the last flagged Phase 5/6 kernel-level gap, now closed. A full
  general-purpose-register save/restore trap handler at its own
  dedicated vector (`0x81`), since the `extern "x86-interrupt"`
  handlers used nearly everywhere else in this kernel don't expose GPRs --
  only enough to report results, not enough to suspend and later
  resume a task's complete state. Two real ring-3 tasks, each with
  their own address space, genuinely suspended and resumed across 20
  real trap-based yields (10 each), proven by a counter kept purely in
  register `r12` (not memory) surviving correctly across every
  activation -- a subtly wrong save/restore would show up as a reset
  or corrupted counter, not a crash. Verified two ways: the real
  mechanism (both counters reach exactly 10) and a negative control
  (deliberately un-mirroring two registers' push/pop order produces
  genuinely wrong values and a correctly reported `MISMATCH`, this
  time matching the prediction exactly, unlike several earlier
  negative controls this session that failed louder/earlier than
  expected). First real boot attempt, zero bugs -- by a wide margin
  the most intricate hand-written assembly in this kernel
- NX (no-execute) enforcement (`memory::paging::enable_nx`,
  `experimental::elf::load` honoring each segment's `PF_X` flag,
  `NO_EXECUTE` added to every stack/private-data mapping across every
  demo, the heap included) -- closes a gap flagged repeatedly since
  Phase 2. Verified with a full regression (every prior demo still
  passes unchanged with NX enabled) plus a genuine **positive**
  control, not just a negative one: a temporary addition wrote a
  single `ret` byte into a heap allocation and called it as a
  function, producing an immediate `PROTECTION_VIOLATION |
  INSTRUCTION_FETCH` page fault -- proof the mechanism actively blocks
  a real violation, not just that removing it would matter
- Real position-independent executable (`ET_DYN`) loading with
  `R_X86_64_RELATIVE` relocation processing -- closes a gap flagged
  since Phase 4. Verified the toolchain first (`readelf -r` on a
  hand-written test program confirmed `ld -pie --no-dynamic-linker`
  produces exactly the relocation structure expected) before writing
  any Rust. `experimental::elf::load` gained a `load_bias` parameter
  (required `0` for fixed-address `ET_EXEC` files, enforced as an
  error), and a new pass applies every `R_X86_64_RELATIVE` entry from
  the file's `PT_DYNAMIC` segment. Loaded at a base address nowhere
  close to the file's own addresses; the program's absolute data
  pointer dereferences correctly. **A real bug found and fixed on
  first boot** (a straightforward omission: a private data page the
  demo needed wasn't mapped, caught immediately by a precise page
  fault report) -- the pattern of quick, real bugs surfacing and
  getting fixed continuing to hold across the session. Verified with a
  negative control on the relocation math itself, not just the demo
  around it
- Real address-space teardown (`memory::frame_allocator`'s new
  free-list, `experimental::address_space::AddressSpace::destroy`
  walking the full private page-table tree) -- closes the gap flagged
  since the isolation demo's introduction ("every address space leaks
  forever via `mem::forget`"). Proof is unambiguous, not just
  plausible: `destroy` frees a space's own PML4 frame last, so
  building a new space immediately afterward should have its PML4
  allocation pop exactly that freed frame back off the list -- verified
  by comparing the actual physical addresses, not just a counter.
  First real boot attempt, zero bugs, for recursive three-level
  page-table freeing coordinated with a brand-new allocator backing.
  Negative control (skipping the PML4 frame's own deallocation)
  matched the prediction exactly: one fewer frame reclaimed than
  baseline, correctly flagged `LEAK`
- **The first genuine Phase 5 capability, not another prerequisite:**
  a minimal PE32+ (Windows) loader (`src/experimental/pe.rs`,
  `pe_demo.rs`). Toolchain verified first, same discipline as the PIE
  work: `ld -m i386pep --subsystem native -e _start` on this system's
  GNU binutils -- no MSVC, no MinGW runtime -- produces a genuine
  PE32+ file, confirmed with `file`/`objdump -p` (real
  `IMAGE_OPTIONAL_HEADER64`, Magic `0x20b`, zero base relocations)
  before any Rust was written. The loader parses DOS/PE/COFF/Optional
  headers and the section table with the same bounds-checking rigor as
  `experimental::elf`, maps sections with real permissions from
  `Characteristics` (W^X enforced), and **rejects outright** any PE
  with nonzero base relocations rather than silently mis-loading it.
  Proof: a real PE binary's own entry point executed in ring 3 and
  wrote a magic value only it could have produced. First real boot
  attempt, zero bugs, for an entirely new file format. Negative
  control (wrong expected value) matched the prediction exactly.
  **What this does and doesn't mean:** a real Windows-format
  executable's code can be loaded and run -- not that Windows
  applications can run. No import resolution, no relocation
  processing (ASLR binaries -- most real Windows software -- are
  rejected by design), no Win32 API surface. The loader-mechanics
  equivalent of `experimental::elf`'s first `ET_EXEC` proof.
- **The first real Phase 6 capability:** a minimal DEX (Android)
  format parser (`src/experimental/dex.rs`, `dex_demo.rs`). A real,
  important scope note made explicit: Android's *native* code already
  uses ELF (`experimental::elf` handles it, no separate loader
  needed) -- DEX is specifically the bytecode *container* format, and
  this module parses that container, **not bytecode execution**
  (which needs a real interpreter/AOT compiler, comparable in scope to
  writing a JVM). No existing toolchain to hand-assemble DEX with, so
  a real, checksum-and-SHA1-signature-correct minimal file was
  hand-constructed with Python's `struct`/`hashlib`/`zlib` and
  independently re-parsed before any Rust touched it. The parser
  includes a genuine structural check (the `map_list` must contain an
  entry that describes itself, not just a nonzero count). Needs no
  address space or ring-3 transition -- pure data validation, so both
  the positive proof and two negative checks (bad magic, corrupted
  self-reference) run as ordinary, **permanent, every-boot** code, not
  a temporary rebuild-and-revert test. First real boot attempt, zero
  bugs

**Last verified boot transcript** (`tools/qemu_boot_test.sh`, passing,
clean from-scratch build):

```
nyrqis kernel: phase 1 bring-up
  gdt: loaded (kernel code segment + tss)
  idt: loaded (divide-error, invalid-opcode, gpf, page-fault, double-fault)
  double-fault ist stack: index 0
  multiboot: memory map parsed, 32639 usable 4KiB frames (127 MiB)
  acpi: RSDP v0 validated (RSDT root table), 4 tables enumerated and checksum-verified
  acpi reclaim: platform reports no AcpiReclaimable region; synthetic control on reserved 0xf0000..0x100000: usable before=false (expected false), after release=true (expected true), after restoring=false (expected false) -- OK
  paging: dynamic mapper live (CR3 read back, OffsetPageTable constructed), NX enabled (EFER.NXE)
  heap: mapped 256 pages (1024 KiB) at 0x10000000000, global allocator live
  heap self-test: Vec<u32> of 100 elements allocated and summed correctly (5050)
  frames allocated so far: 259
  memory tlb-demo: mapped (frame Some(PhysAddr(0x277000))), unmapped (translate -> None), remapped (frame Some(PhysAddr(0x27b000))); re-read 0xbbbbbbbbbbbbbbbb (expected 0xbbbbbbbbbbbbbbbb)
  memory tlb-demo self-test: present->absent->present OK, backing frame genuinely changed OK, remap read back new bytes OK -- OK
  pic: remapped (IRQ0-7 -> 0x20-0x27, IRQ8-15 -> 0x28-0x2F), timer unmasked
  interrupts: enabled (sti)
  timer self-test: 20 real hardware timer interrupts observed and handled
  rtc self-test: 4 additional RTC periodic interrupts observed and handled after a snapshot (1086 total; IRQ8, via the slave-PIC cascade)
nyrqis kernel: phase 1+2+3-prep complete, boot phase = 7 (Ready)
  experimental scheduler: 3 tasks registered (pre-normative, not NPS-002 conformant)
  experimental ipc: 3 mailboxes initialized (pre-normative, not NPS-003 conformant)
    [experimental task A] activation 1: sent IPC message to task B
    [experimental task B] activation 1: received IPC message {sender: 1, seq: 1} -- OK
    ... (all six round trips deliver correctly, in order) ...
    [experimental task A] activation 6: sent IPC message to task B
    [experimental task B] activation 6: received IPC message {sender: 1, seq: 6} -- OK
  experimental scheduler self-test: 6 round trips completed, task A ran 6 times, task B ran 6 times
  experimental ipc self-test: 6 messages sent task A -> task B and received correctly, 0 mismatches
  experimental isolated-tasks: 5 total tasks in round-robin (main, A, B, C[own cr3 0x1a3000], D[own cr3 0x1a8000])
    ... (A/B's IPC demo continues interleaving correctly for 50 more rounds) ...
  experimental isolated-tasks self-test: C 50 ok / 0 failed, D 50 ok / 0 failed (across 50 switches each, repeated CR3 reload)
  experimental dex-demo: crypto known-answer -- Adler-32("Wikipedia")=0x11e60398 (expected 0x11e60398) OK, SHA-1("abc") 20-byte digest OK
  experimental dex-demo: parsed a real DEX file (file_size=128, map_entry_count=1, all section counts zero as constructed) -- OK
  experimental dex-demo: Adler-32 checksum + SHA-1 signature verified in-kernel -- OK
  experimental dex-demo: corrupted magic -- parse result Some(BadMagic), correctly rejected: true
  experimental dex-demo: corrupted map self-reference -- parse result Some(NoMapListEntry), correctly rejected: true
  experimental dex-demo: corrupted checksum field -- verify result Some(BadChecksum), correctly rejected: true
  experimental dex-demo self-test: crypto known-answer OK, valid parse+integrity OK, bad-magic rejection OK, map-self-reference rejection OK, checksum rejection OK -- OK
  experimental teardown-demo: space X used (wrote+read 0xdeadc0dedeadc0de -- OK), 5 frames reclaimed on destroy (279 -> 274 in-use, baseline 274) -- fully reclaimed
  experimental teardown-demo: space Y's PML4 (0x1c6000) == space X's just-freed PML4 (0x1c6000) -- OK, frame genuinely reused
  experimental ring3-scheduler: 3 tasks built (E entry 0x50000000000; F, G pre-loaded with initial trap blobs), target 21 total yields (7 per task)
  experimental ring3-scheduler self-test: task E counter=7 (expected 7) -- OK
  experimental ring3-scheduler self-test: task F counter=7 (expected 7) -- OK
  experimental ring3-scheduler self-test: task G counter=7 (expected 7) -- OK
  experimental ring3-scheduler self-test: 21 real trap-based yields processed, all 3 tasks' full register + address-space state correctly suspended and resumed -- OK
  experimental ring3-scheduler teardown: task F's address space (pml4 0x297000, active for its own 7 trap-based yields) destroyed after suspension -- 10 frames reclaimed (305 -> 295 in use) -- OK
  experimental pie-demo: ET_DYN loaded at base 0x60000000000, entry 0x60000001000 (bias correctly applied by the loader, not baked into the file)
  experimental pie-demo self-test: relocated pointer dereferenced to 0xfeedface12345678 (expected 0xfeedface12345678) -- OK, relocation correctly applied
  experimental pe-demo: PE32+ loaded at its own ImageBase (no relocation), entry 0x140001000
  experimental pe-demo self-test: PE entry point wrote 0xfeedc0defeedc0de (expected 0xfeedc0defeedc0de) -- OK, a genuine Windows-format (PE32+) executable's own code ran in ring 3
  experimental isolation: address space A built (pml4 0x1c1000), program A loaded, entry 0x20000000000
  experimental isolation: trap from process A (cpl=3), read 0xc0ffee (expected 0xc0ffee) -- OK
  experimental isolation: address space B built (pml4 0x1c7000), program B loaded, entry 0x20000000000 -- switched active, A is now inactive but preserved
  experimental isolation: trap from process B (cpl=3), read 0xbbbbbbbb (expected 0xbbbbbbbb) -- OK
  experimental isolation: switched back to process A's address space, re-read 0xc0ffee (expected 0xc0ffee, unchanged since A ran) -- OK, ISOLATED
  experimental isolation self-test: two independent address spaces verified mutually isolated -- OK
  memory guard-demo: stack mapped at 0x80000000000..0x80000004000 (4 pages); bottom present OK, top present OK, guard page 0x7fffffff000 absent OK, page above 0x80000004000 absent OK
  memory guard-demo self-test: guarded stack correctly built (4 usable pages, page below genuinely unmapped, page above genuinely unmapped) -- OK
  memory guard-demo: arming terminal overflow probe -- writing one qword at the guard page 0x7fffffff000 (expect a PROTECTION_VIOLATION page fault)
  memory guard-demo self-test: overflow write to guard page 0x7fffffff000 faulted exactly as required (accessed 0x7fffffff000, error code PageFaultErrorCode(CAUSED_BY_WRITE)) -- OK, a stack overflow is caught deterministically instead of corrupting memory below it
nyrqis kernel: phase 1+2+3+4+ring3-elf-isolation-poc+guard-pages complete
PASS: kernel reached BootPhase::Ready and exited cleanly
```

**Negative controls** (all temporary, reverted immediately after --
not shipped): confirms the permission model and correctness checks are
actually enforced, not decorative or coincidentally never exercised.
The third and fourth ones both produced honest surprises -- documented
as they actually happened rather than adjusted to match the original
predictions: both were expected to demonstrate silent data corruption,
and both instead failed louder and earlier.

```
# USER_ACCESSIBLE removed from the mapping flags:
EXCEPTION: PAGE FAULT
accessed address: VirtAddr(0x20000000000)
error code: PROTECTION_VIOLATION | USER_MODE

# EXPECTED_MAGIC deliberately set wrong:
  experimental usermode self-test: FAILED -- cpl=3 (expected 3), magic=0xc0ffee (expected 0xdeadbeef)

# PML4 index covering the user-code region deliberately shared between
# A and B (simulating the isolation bug this demo exists to catch) --
# expected silent corruption on the isolation re-check; got a clean
# panic during B's setup instead, since B's mapping attempt collided
# with A's already-present entries in the now-shared table:
KERNEL PANIC: panicked at src/experimental/isolation_demo.rs:135:37:
failed to load program B: Elf(MappingFailed)

# switch_to's "mov cr3, rdx" reload disabled (simulating a scheduler
# that switches stacks/registers but forgets to switch address
# spaces) -- expected task C to silently read/write wrong data;
# instead its very first write to its own "private" address
# immediately faulted, since that address was never mapped in
# whichever space was actually still active:
EXCEPTION: PAGE FAULT
accessed address: VirtAddr(0x30000000000)
error code: CAUSED_BY_WRITE

# ring3_yield_trap_entry's push order for r11/r12 swapped without
# swapping the corresponding pop order (corrupting exactly the
# register the counter test relies on) -- this one matched the
# prediction exactly, unlike the two above: genuinely wrong values,
# correctly flagged, rest of the boot sequence unaffected:
  experimental ring3-scheduler self-test: task E counter=1115125 (expected 10) -- MISMATCH, task F counter=5 (expected 10) -- MISMATCH
  experimental ring3-scheduler self-test: FAILED
  experimental isolation: address space A built (pml4 0x1c1000), program A loaded, entry 0x20000000000
  [... isolation_demo's own independent self-test continues and still passes ...]
```

**Positive control** (a different kind of evidence than the negative
controls above -- this confirms a mechanism actively blocks a real
violation, rather than confirming something stops working when the
mechanism is removed). Temporary, reverted immediately after -- not
shipped: a single `ret` instruction byte written into a heap
allocation, called as a function, with NX enabled:

```
  NX positive-control: attempting to execute from heap memory at 0x0000010000000000 (expect an immediate instruction-fetch page fault)
EXCEPTION: PAGE FAULT
accessed address: VirtAddr(0x10000000000)
error code: PROTECTION_VIOLATION | INSTRUCTION_FETCH
```

**Negative control on PIE relocation** (temporary, reverted immediately
after -- not shipped): omitting the load bias from a relocation's
written value didn't produce a plausible-looking wrong value -- it made
the pointer resolve to the raw file-relative address, unmapped in the
real address space, so it faulted immediately rather than silently
reading garbage:

```
EXCEPTION: PAGE FAULT
accessed address: VirtAddr(0x3000)
error code: PROTECTION_VIOLATION | USER_MODE
```

**Negative control on address-space teardown** (temporary, reverted
immediately after -- not shipped): deliberately skipping the PML4
frame's own deallocation matched the prediction exactly -- one fewer
frame reclaimed than baseline, correctly flagged, reuse check
correctly reporting a mismatch:

```
  experimental teardown-demo: space X used (wrote+read 0xdeadc0dedeadc0de -- OK), 4 frames reclaimed on destroy (82 -> 78 in-use, baseline 77) -- LEAK
  experimental teardown-demo: space Y's PML4 (0x1b8000) != space X's just-freed PML4 (0x1b6000) -- not reused
KERNEL PANIC: panicked at src/lib.rs:203:5:
address-space teardown did not fully reclaim its frames, or the reclaimed frame wasn't reused
```

**Negative control on the PE loader** (temporary, reverted immediately
after -- not shipped): deliberately setting the kernel's expected
value wrong matched the prediction exactly -- a clean, correctly
reported mismatch, with the rest of the demo chain unaffected:

```
  experimental pe-demo self-test: PE entry point wrote 0xfeedc0defeedc0de (expected 0xdeadbeefdeadbeef) -- MISMATCH
```

## Gap-closure increment (2026-10-08): TLB-on-unmap, DEX integrity, 3 ring-3 tasks

Four documented gaps from the "does NOT exist yet" list below were
closed in this increment, each with its own **every-boot** self-test --
the evidence runs on every boot, not just once during development:

- **TLB invalidation on unmap** (`memory::paging::unmap_page` +
  `memory::tlb_demo`). `unmap_page` passes the mapper's flush through,
  which emits `invlpg`; the demo maps a page (frame A), unmaps it
  (`translate` -> `None`), remaps the *same* virtual address (frame B),
  and reads back frame B's bytes.
- **DEX checksum + signature verification** (`experimental::crypto`:
  in-kernel Adler-32 and SHA-1; `experimental::dex::verify`). Proven
  three ways: known-answer vectors for both primitives
  (`Adler-32("Wikipedia") == 0x11e60398`, `SHA-1("abc")` == the
  published digest), the real bundled `classes.dex` verifying in-kernel,
  and a corrupted-checksum negative check that must reject with exactly
  `BadChecksum`.
- **Three ring-3 tasks** (was two). `experimental::ring3_scheduler` is
  generalized to N tasks in strict round-robin; 21 traps / 3 tasks = 7
  yields each, all three counters verified.
- **Heap headroom**: the third ring-3 address space tipped the demo
  chain past the old 256KiB heap (a real "memory allocation failed"
  panic in `isolation_demo`, caught by the boot test the moment the
  third task landed). The heap is now 1MiB.

Verified transcript lines (from `tools/qemu_boot_test.sh`, passing):

```
  heap: mapped 256 pages (1024 KiB) at 0x10000000000, global allocator live
  memory tlb-demo: mapped (frame Some(PhysAddr(0x1b7000))), unmapped (translate -> None), remapped (frame Some(PhysAddr(0x1bb000))); re-read 0xbbbbbbbbbbbbbbbb (expected 0xbbbbbbbbbbbbbbbb)
  memory tlb-demo self-test: present->absent->present OK, backing frame genuinely changed OK, remap read back new bytes OK -- OK
  experimental dex-demo: crypto known-answer -- Adler-32("Wikipedia")=0x11e60398 (expected 0x11e60398) OK, SHA-1("abc") 20-byte digest OK
  experimental dex-demo: Adler-32 checksum + SHA-1 signature verified in-kernel -- OK
  experimental dex-demo: corrupted checksum field -- verify result Some(BadChecksum), correctly rejected: true
  experimental dex-demo self-test: crypto known-answer OK, valid parse+integrity OK, bad-magic rejection OK, map-self-reference rejection OK, checksum rejection OK -- OK
  experimental ring3-scheduler: 3 tasks built (E entry 0x50000000000; F, G pre-loaded with initial trap blobs), target 21 total yields (7 per task)
  experimental ring3-scheduler self-test: task E counter=7 (expected 7) -- OK
  experimental ring3-scheduler self-test: task F counter=7 (expected 7) -- OK
  experimental ring3-scheduler self-test: task G counter=7 (expected 7) -- OK
  experimental ring3-scheduler self-test: 21 real trap-based yields processed, all 3 tasks' full register + address-space state correctly suspended and resumed -- OK
```

## Gap-closure increment (2026-10-09): RTC IRQ + guard pages

Two more documented gaps from the "does NOT exist yet" list below were
closed, both NPS-001-grounded (not gated on NPS-002/003):

- **Second real hardware IRQ line** (`src/rtc.rs`): the CMOS/RTC
  periodic interrupt on IRQ8. `pic.rs` gained a real `unmask(irq)`
  (until now exactly one line was ever unmasked, deliberately -- see
  its module doc's "unmasking a line with no handler is worse than
  not supporting it"). The self-test measures a *delta* (waits for N
  more ticks after a snapshot), so a stale count from before the test
  can't masquerade as success. The RTC was chosen for this line because
  it needs no input to test at all (the keyboard came later and is
  proven input-free by 8042 output-buffer injection -- see the
  keyboard/ATA increment below).
- **Guard pages for stack-overflow detection** (`memory::guard_demo`):
  a `GuardedStack` primitive (`build`) that maps N usable pages and
  leaves the page immediately below unmapped, plus an every-boot
  self-test. The enforcement half is *terminal by design* -- it
  genuinely writes into the guard page and the page-fault handler
  (`idt::page_fault_handler`, via `guard_demo::fatal_page_fault`)
  confirms the expected `CAUSED_BY_WRITE` fault. If the write doesn't
  fault, `run_and_prove` reports failure instead, so it can't pass by
  the mechanism doing nothing.
- **Address-space teardown of an ever-active ring-3 space**
  (`ring3_scheduler`'s finale): after task F has been the active space
  for its own seven `int 0x81` yields and is suspended for good, its
  `AddressSpace` is destroyed and the frame allocator's in-use count
  is checked before/after. This closes the sharp edge `destroy`'s own
  safety comment named ("must not be called on a space some suspended
  task might still be resumed into"). Deliberately *not* the
  just-trapped task: control is running on that task's kernel stack,
  so freeing its stack here would free the memory being executed on.
- **ACPI-reclaimable memory reuse** (`src/acpi.rs` +
  `memory::frame_allocator`): RSDP checksum validation, RSDT/XSDT table
  enumeration with per-table checksum verification, and an allocator
  that refuses `AcpiReclaimable` regions until `reclaim_acpi` releases
  them. No QEMU machine type reports such a region, so the release
  contract is proven by an explicitly-labelled synthetic control
  (refused -> usable -> refused again); the RSDP/table path is real
  every-boot evidence.

Verified transcript lines (from `tools/qemu_boot_test.sh`, passing):

```
  rtc: periodic interrupt armed at 1024 Hz, IRQ8 + cascade IRQ2 unmasked
  acpi: RSDP v0 validated (RSDT root table), 4 tables enumerated and checksum-verified
  acpi reclaim: platform reports no AcpiReclaimable region; synthetic control on reserved 0xf0000..0x100000: usable before=false (expected false), after release=true (expected true), after restoring=false (expected false) -- OK
  rtc self-test: 4 additional RTC periodic interrupts observed and handled after a snapshot (1086 total; IRQ8, via the slave-PIC cascade)
  experimental ring3-scheduler teardown: task F's address space (pml4 0x297000, active for its own 7 trap-based yields) destroyed after suspension -- 10 frames reclaimed (305 -> 295 in use) -- OK
  memory guard-demo: stack mapped at 0x80000000000..0x80000004000 (4 pages); bottom present OK, top present OK, guard page 0x7fffffff000 absent OK, page above 0x80000004000 absent OK
  memory guard-demo self-test: guarded stack correctly built (4 usable pages, page below genuinely unmapped, page above genuinely unmapped) -- OK
  memory guard-demo: arming terminal overflow probe -- writing one qword at the guard page 0x7fffffff000 (expect a PROTECTION_VIOLATION page fault)
  memory guard-demo self-test: overflow write to guard page 0x7fffffff000 faulted exactly as required (accessed 0x7fffffff000, error code PageFaultErrorCode(CAUSED_BY_WRITE)) -- OK, a stack overflow is caught deterministically instead of corrupting memory below it
```

**Negative control on the guard-page check** (temporary, reverted
immediately after -- not shipped): mapping the would-be guard page as
well (a broken guard) made the check correctly report failure and the
boot test exit nonzero, rather than silently passing:

```
  memory guard-demo: stack mapped at 0x80000000000..0x80000004000 (4 pages); bottom present OK, top present OK, guard page 0x7fffffff000 absent FAILED, page above 0x80000004000 absent OK
  memory guard-demo self-test: guarded stack correctly built (4 usable pages, page below genuinely unmapped, page above genuinely unmapped) -- FAILED
  memory guard-demo self-test: FAILED -- guarded stack was built but its page-table state is wrong
FAIL: unexpected QEMU exit status 35
```

## Gap-closure increment (2026-10-09): guard pages under the live ring-3 kernel stacks

The guard-page mechanism was proven above, but it only guarded a
dedicated probe stack; the stacks live ring-3 tasks actually trap onto
(`AddressSpace`'s RSP0 stack) were plain heap `Vec<u8>` allocations with
no guard. This closes that gap for the ring-3 stacks.

- **`memory::kernel_stack`** -- a shared region at `0x140_0000_0000`
  inside PML4 index 2 (already shared by every address space), where
  guarded stacks are carved out: each stack gets `N` usable pages with
  the page directly below left permanently unmapped. The region has to
  be shared because a cooperative switch changes `CR3` *before* `RSP`:
  the CPU keeps executing on the outgoing task's stack for a few
  instructions after the new address space is active, so that stack
  must stay mapped under the incoming space's tables. It cannot live
  in the heap -- unmapping a page the heap allocator still owns would
  corrupt it -- hence a dedicated region under the same shared index.
- **`experimental::address_space`** -- `AddressSpace` no longer holds a
  heap `Vec<u8>`; its RSP0 stack is now a `kernel_stack::GuardedStack`.
  `destroy` reclaims it too (clearing the shared page-table entries and
  freeing the frames), since the region hands out a fresh slot per
  allocation and never reuses one.
- **`memory::guard_demo`** -- the terminal enforcement probe now
  allocates through `kernel_stack::allocate`, the same allocator the
  live stacks use, so the fault proof covers the real mechanism rather
  than a look-alike.
- **`experimental::ring3_scheduler`** -- after building tasks E/F/G,
  verifies, via `translate`, that each live task's RSP0 stack has every
  page present and its guard page genuinely absent.

Verified transcript lines (from `tools/qemu_boot_test.sh`, passing):

```
  experimental teardown-demo: space X used (wrote+read 0xdeadc0dedeadc0de -- OK), 10 frames reclaimed on destroy (296 -> 286 in-use, baseline 286) -- fully reclaimed
  experimental teardown-demo: space Y's PML4 (0x29d000) == space X's just-freed PML4 (0x29d000) -- OK, frame genuinely reused
  experimental ring3-scheduler: task E kernel RSP0 stack 0x14000019000..0x1400001e000 (5 pages) has guard page 0x14000018000 unmapped -- OK
  experimental ring3-scheduler: task F kernel RSP0 stack 0x1400001f000..0x14000024000 (5 pages) has guard page 0x1400001e000 unmapped -- OK
  experimental ring3-scheduler: task G kernel RSP0 stack 0x14000025000..0x1400002a000 (5 pages) has guard page 0x14000024000 unmapped -- OK
  experimental ring3-scheduler self-test: all 3 live task kernel stacks carry a genuinely unmapped guard page directly below them -- OK
  experimental ring3-scheduler teardown: task F's address space (pml4 0x2b2000, active for its own 7 trap-based yields) destroyed after suspension -- 15 frames reclaimed (337 -> 322 in use) -- OK
  memory guard-demo: stack mapped at 0x14000049000..0x1400004d000 (4 pages); bottom present OK, top present OK, guard page 0x14000048000 absent OK, page above 0x1400004d000 absent OK
  memory guard-demo self-test: overflow write to guard page 0x14000048000 faulted exactly as required (accessed 0x14000048000, error code PageFaultErrorCode(CAUSED_BY_WRITE)) -- OK, a stack overflow is caught deterministically instead of corrupting memory below it
```

**A real bug this increment surfaced, and its fix.** Moving stacks out
of the heap and into frame-allocator-backed shared mappings initially
made `experimental::teardown_demo` fail its "fully reclaimed" assertion
(5 frames leaked per destroyed space, caught by the boot test), because
`destroy` freed a space's private tables/data but not its
now-frame-backed stack. Fixed at the cause: `destroy` now unmaps the
stack's (shared) entries and frees its frames, restoring full
reclamation (`10 frames reclaimed (296 -> 286 in-use, baseline 286) --
fully reclaimed`).

Honest scope: the live ring-3 RSP0 stacks are verified **structurally**
(page-table state), not by deliberately overflowing a live task -- that
would end the boot mid-demo. The terminal *enforcement* proof still
uses a dedicated probe stack, but now allocated through the same
`kernel_stack` allocator.

## Gap-closure increment (2026-10-09): guard pages under the ring-0 scheduler task stacks

The cooperative scheduler's `spawn`/`spawn_with_cr3` used a heap
`Vec<u8>` for each task stack -- the last live kernel stacks without a
guard. They now allocate from the same shared `memory::kernel_stack`
region:

- `spawn`/`spawn_with_cr3` take a `mapper` + `frame_allocator` (any
  space's mapper works -- the region is shared), and
  `TaskControlBlock` holds an `Option<GuardedStack>` (`None` only for
  task 0, which runs on the kernel's own boot stack).
- `experimental::address_space::active_mapper` exposes a mapper for
  whichever space is active, for the one call site
  (`experimental::isolated_tasks`) that spawns tasks without owning a
  specific `AddressSpace`.
- `lib.rs` calls `experimental::scheduler::verify_guarded_stacks` after
  tasks A-D are spawned, checking each stack structurally via the page
  tables.

Verified transcript lines (from `tools/qemu_boot_test.sh`, passing):

```
  experimental scheduler: task 1 kernel stack 0x14000001000..0x14000005000 (4 pages) has guard page 0x14000000000 unmapped -- OK
  experimental scheduler: task 2 kernel stack 0x14000006000..0x1400000a000 (4 pages) has guard page 0x14000005000 unmapped -- OK
  experimental scheduler: task 3 kernel stack 0x14000017000..0x1400001b000 (4 pages) has guard page 0x14000016000 unmapped -- OK
  experimental scheduler: task 4 kernel stack 0x1400001c000..0x14000020000 (4 pages) has guard page 0x1400001b000 unmapped -- OK
  experimental scheduler self-test: all 4 task kernel stacks carry a genuinely unmapped guard page directly below them -- OK
```

This adds one more instance of the pre-existing "shared reference to
mutable static" warning (`TASKS.iter()` in `verify_guarded_stacks`),
same category the file already emits and deliberately leaves visible.

## Gap-closure increment (2026-10-09): keyboard (IRQ1) and ATA/IDE (IRQ14/15) handlers

The disposition's last tractable item -- "more IRQ lines with real
handlers" -- is now closed for two more lines, each proven every boot:

- **`src/keyboard.rs`** -- the PS/2 (i8042) keyboard on IRQ1. The
  apparent blocker ("an input IRQ can't be tested automatically") is
  resolved without any external input: the controller's own "Write
  Keyboard Output Buffer" command (`0xD2`) makes the *guest* place a
  known scancode in the output buffer, raising IRQ1 exactly as a real
  keystroke would. The self-test injects `0x1e` and confirms the
  handler read it back.
- **`src/ata.rs`** -- the ATA/ATAPI IDE channels on IRQ14 (primary) and
  IRQ15 (secondary). It probes for a device, enables interrupts, and
  issues a real `IDENTIFY (PACKET) DEVICE`, adapting the command
  (`0xEC` vs `0xA1`) to whatever the drive actually completes and
  reporting the completion status. Because it depends on an attached
  device, it is conditional: with no device it reports that and leaves
  the lines masked.

Verified transcript lines (from `tools/qemu_boot_test.sh`, passing):

```
  keyboard: 8042 keyboard interrupt (IRQ1, master PIC) enabled and unmasked
  keyboard self-test: injected scancode 0x1e via the 8042 output-buffer command (0xD2), 1 interrupt(s) handled, handler read back 0x1e -- OK
  ata self-test: secondary channel master detected, IDENTIFY (0xa1) completed with status 0x58 after 2 interrupt(s) on IRQ15 -- OK, data-ready
```

**A real, latent deadlock found and fixed while adding the ATA line.**
The IDE channel is unmasked *after* `sti`, and that exposed a genuine
bug in `pic::unmask`: it locks the `PICS` spin mutex, which IRQ
handlers also lock (`end_of_interrupt`). Called with interrupts
enabled, a timer/RTC interrupt arriving while `unmask` held that lock
would leave the handler spinning forever on a lock the interrupted
code could never release -- a classic same-CPU spinlock deadlock.
Every earlier `unmask` call happened before `sti`, so the bug was
latent until a post-`sti` caller appeared. It showed up as an
*intermittent* boot hang (roughly one run in four) right at the ATA
unmask, and was fixed at the cause by disabling interrupts around the
lock in `pic::unmask` (`without_interrupts`); five consecutive boots
pass after the fix. (The intermediate `identify` retry -- `0xEC` abort
on the ATAPI drive, then `0xA1` succeeding with `DRDY|DRQ` -- is also
visible in the transcript's adaptive behavior.)

(No new compiler-warning categories: `keyboard.rs` and `ata.rs` use
`AtomicU64` statics rather than `static mut`, so they add none.)

## Gap-closure increment (2026-10-09): timer-driven (preemptive) ring-3 task switching

The last open *mechanism* gap -- "ring-3 switching is cooperative-only:
a ring-3 task must itself `int 0x81` to yield, nothing forces it to" --
is now closed, explicitly pre-normatively (this is still NPS-002
`Draft` territory: the mechanism was built, not any policy).

**Design.** The timer's IDT entry (vector 0x20) was an
`extern "x86-interrupt"` handler -- an ABI that exposes only the
architectural frame (RIP/CS/RFLAGS/RSP/SS), enough to count a tick,
not enough to preempt a task, which needs every GPR saved and later
restored. It is now a raw `#[naked]` handler
(`ring3_scheduler::timer_entry`) that pushes the *same 15 registers in
the same order* as the existing `int 0x81` yield trap, so an interrupt
taken from ring 3 produces the byte-identical 20-slot saved blob the
resume path already understands, and it resumes the interrupted
context unchanged in every non-preempted case:

- **ring 0** (all kernel code, all phases): tick + EOI + resume --
  exactly what the old handler did;
- **ring 3 outside the proof phase**: same -- a tick lands on the
  running task and resumes it, so the E/F/G cooperative round-robin
  and every later ring-3 demo behave exactly as before;
- **ring 3 with preemption armed**: the same
  save/next/activate/return round-robin step as
  `ring3_yield_dispatch`, only with the clock as the reschedule point.

A SysV 16-byte stack-alignment pad (0 or 8 bytes, branch-free) sits
strictly *below* the saved blob so the documented layout never
changes.

**The proof (why a PASS cannot be a false pass).** `finale` builds two
FRESH ring-3 tasks (H, I) from `userprog/prog_spin.s` -- an infinite
loop with **no `int` instruction anywhere in it** -- arms preemption,
and enters H. Since H never yields, task I can only ever run if IRQ0
force-switches the CPU off H; and since neither task ever terminates,
the boot can only complete if the switch count reaches its target and
`preempt_finale` chains onward. If preemption were broken the hogs
would spin forever and `tools/qemu_boot_test.sh` would time out
(FAIL); if the counts were wrong, `preempt_finale`'s `assert!` would
panic (FAIL). On top of that, the self-test pins exact,
timing-independent numbers: strict round-robin over two tasks means
each is forcibly preempted exactly `PREEMPT_TARGET / 2` times, and the
cooperative-yield counter must be unchanged across the whole phase.

Verified transcript lines (from `tools/qemu_boot_test.sh`, 5/5
consecutive passing boots):

```
  experimental ring3-scheduler: timer preemption armed -- entering task H, whose program never traps; IRQ0 is the only reschedule point from here (target 6 switches)
  experimental ring3-scheduler preemption self-test: task H counter=26052309 (>0 required -- its program contains no trap instruction, so only a timer switch could ever let it run) -- OK
  experimental ring3-scheduler preemption self-test: task I counter=36609835 (>0 required -- its program contains no trap instruction, so only a timer switch could ever let it run) -- OK
  experimental ring3-scheduler preemption self-test: task H force-switched off the CPU 3 times, task I 3 times (6 timer-driven switches, target 6; 0 cooperative yields during the phase) -- OK
  experimental ring3-scheduler preemption self-test: two non-yielding ring-3 tasks round-robined purely by IRQ0 -- timer-driven preemption, not cooperative-only -- OK
```

The cooperative E/F/G phase above it is unchanged (21 voluntary
yields, exact 7/7/7 counts); the new H/I kernel RSP0 stacks carry
verified guard pages like every other live stack; and the whole demo
chain still runs to completion (pie/PE/isolation/guard-demo, exit 0).
Warning accounting: no new categories -- and one pre-existing
`static mut` reference warning was *removed* along the way by writing
a frame-allocator access through a raw pointer, so the build now emits
33 lib warnings where it used to emit 34. An honest refactoring side
effect, not a suppression: no `#[allow]` was added anywhere.

## Gap-closure increment (2026-10-19): PE32+ import resolution -- a real DLL's export directory and a real client's kernel-resolved IAT

The Windows gap "loading a PE's own code is a loader -- but with no import
resolution, real Windows-format binaries that need imports still won't run"
now loses its import-resolution half. `experimental::pe_import_demo` proves
the real Windows import/export machinery, end to end and every boot, on a
real `ld -m i386pep` pair built by `build.sh`:

- **The provider and its export table are real, and read the real way.**
  A genuine Windows-format DLL (`userprog_pe_import_dll.dll`,
  `ld -m i386pep --shared --enable-auto-import`) loads at its own
  `ImageBase 0x180000000000`, and `pe::DllExportTable` parses its REAL
  `IMAGE_DIRECTORY_ENTRY_EXPORT` directory -- the actual GetProcAddress
  mechanism, not a synthetic name->address map. Facts checked every boot:
  the directory declares the DLL's own name, carries exactly the two
  exported names, `lookup(crossfn)` answers `provider + 0x1000` (the
  provider's own `.text`) and `lookup(crossmark)` answers
  `provider + 0x2000` (its own `.data`), and an absent symbol answers
  `None`.
- **The client's imports are resolved through its REAL import
  directory.** The dependent (`userprog_pe_import_app.exe`,
  `ld -m i386pep --subsystem native`) is linked DIRECTLY against the
  DLL -- GNU ld synthesizes the client's `.idata`/IAT itself from the
  link-line dependency; no dlltool/import-library step exists or is
  needed on this host. `pe::resolve_imports` walks its REAL
  `IMAGE_DIRECTORY_ENTRY_IMPORT` descriptors and patches both IAT
  slots: `IAT[0x140000002040] = 0x180000001000` (`crossfn`) and
  `IAT[0x140000002048] = 0x180000002000` (`crossmark`), each value
  equal to the independent export lookup and read back unchanged.
- **The client's own code consumes the imports the faithful Windows
  way, in ring 3.** It loads `__imp_crossmark(%rip)` (the resolved DATA
  export's address out of its IAT slot), dereferences the provider's
  data through it, and `call *__imp_crossfn(%rip)` (indirect call
  THROUGH the resolved FUNCTION export's slot). After the client's one
  `int $0x8D` trap both results are checked:
  `0xace5f00dd1ce5eed` (the provider's data immediate) and
  `0xfa571ae5` (the provider's own code's return value). The chain then
  continues into `isolation_demo` as before (no halt).
- **Both negative controls are typed refusals, permanent, every boot.**
  An EMPTY registry refuses the whole import walk with
  `PeError::MissingDllImport`; the DLL name bound to the WRONG FILE
  (the client itself, which has no export directory) refuses
  per-symbol with `PeError::MissingImport`. The second is the sharp
  edge the registry's name-vs-file distinction exists for: the identity
  is the FILE, not whatever name a session may register under it.
- **The build gate pins the pair before the kernel sees it.**
  `tools/verify_prog_layout.sh` fact-checks both embedded artifacts
  from real `objdump` output (provider export-directory sizes, client
  `ImageBase 0x140000000000`, the real import directory with the pinned
  DLL name and both hint/name entries, both `__imp_*` IAT definitions)
  and pins the client text's `int $0x8d`; the indirect call site is
  asserted at RUNTIME instead of pinned by disassembly (ld emits
  `call *0x8d(%rip)` -- GOT-relative, no literal `__IAT_start__` --
  so the exact call encoding is a toolchain fact, not a source one).
- **The silent fault, found and fixed at cause.** The first confident
  boots of this demo ended in a clean ring-3 entry (RIP in the client,
  CS=27) followed by a PAGE FAULT on the client's very first slot
  write -- instead of the markers. The bisect (pre-entry and fault-time
  CR3/translate probes, then a full PML4 sweep at both moments) proved
  the surprising negative: at fault time CR3 was unchanged and every
  nonzero PML4 entry was IDENTICAL to the pre-entry state -- nothing
  was ever erased or remapped; the faulting address walked PML4 index
  8, whereas `SLOT_A = 0x4000_0000_0000` lives under index 128. The
  kernel-side mapping was right the whole time; the CLIENT assembly's
  slot constants were 16x off -- the same 11-vs-12-digit constant
  class that bit other demo artifacts earlier
  (`0x40000000000` vs `0x400000000000`). Fixed in the client source;
  all transient bisect instrumentation removed afterward, including
  the one compiler warning it left behind (the build is as clean as it
  was before the probe was added). The demo's slot rule is now ALSO a
  fact the demo itself restates: `SLOT_A = 0x4000_0000_0000`
  (PML4 index 128 -- a private index, deliberately outside the shared
  PML4[0] identity subtree), `SLOT_B = SLOT_A + 8` inside the same
  page, stack at `0x4000_0001_0000`; one 1-page mapping covers both
  slots. And the rule is a CI gate pin, not prose (same session, after
  the demo itself was proven): `tools/verify_prog_layout.sh` parses
  BOTH independent constant copies (the client assembly's `.set`
  values and the demo driver's consts), refuses any divergence
  between them, refuses any slot/stack constant outside the 47-bit
  `2^46` private-index-128 12-digit class (the historical 11-digit
  `2^42` value is bit_length 43 and dies at BUILD time), and pins
  `SLOT_B = SLOT_A + 8` -- so this hang class can never again pass
  every runtime fact and still hang the boot.
- **Map/flags parity with the proven paths.** The slot page and the
  user stack are mapped user-accessible + NX with the same one
  `map_range` walk every other ring-3 entry uses (isolation_demo,
  pe-demo, pe-reloc-demo), and the client enters through
  `usermode::enter_with_stack` -- the same bare `iretq` mechanism all
  the other booted ring-3 demos went through. Nothing in this increment
  is new transport; the new things are the export-directory parse, the
  import-directory resolution, and the faithful IAT consumption.

Deliberate scope (same honest posture as `pe.rs` itself): no delay-load,
no bound imports, and no Win32 API surface. (Both import items WERE in
this list and are closed by the 2026-10-20 and 2026-10-21 increments
below.) Two items WRITTEN into this
list when the increment landed -- "no forwarding exports (the export
walk refuses an EAT entry pointing back inside the directory rather
than mis-resolving it)" and "ordinal imports typed
`UnsupportedOrdinalImport`" -- were closed the SAME DAY by the second
increment immediately below.

The passing boot's pe-import lines (full transcript in the increment
for the pe-reloc work and the chain order):

```
  experimental pe-import-demo: provider loaded at its own ImageBase 0x180000000000; its export table declares "userprog_pe_import_dll.dll"
  experimental pe-import-demo: export table facts -- 2 name(s), lookup(crossfn) = provider+0x1000, lookup(crossmark) = provider+0x2000
  experimental pe-import-demo: resolved 2 import(s) through the REAL import directory -- OK: each slot's value == the independent export lookup, and reads back unchanged
  experimental pe-import-demo: resolved crossmark address dereferences to 0xace5f00dd1ce5eed (expected 0xace5f00dd1ce5eed, the provider's own .data immediate) -- OK
  experimental pe-import-demo self-test: imported data read 0xace5f00dd1ce5eed through the resolved IAT slot (expected 0xace5f00dd1ce5eed); imported function returned 0xfa571ae5 (expected 0xfa571ae5) -- OK, a real Windows-format executable's own imports resolved through a real Windows-format DLL's own export table (GetProcAddress), and its own code used them in ring 3
```

## Gap-closure increment (2026-10-19, second of the day): forwarded exports and ordinal imports -- the PE import resolver is GetProcAddress-complete

The two deliberate-scope items the first pe-import increment wrote into
its own honest-scope list are now closed, end to end and every boot:

- **Forwarded exports resolve through the registry, depth-capped.
  The loader rule is spec'd exactly: an EAT value inside the export
  directory is NOT an address -- it is a "dll.symbol" STRING reference.
  The per-table `lookup` still answers `None` for it (a registry-
  agnostic table cannot know whether the named DLL exists); the
  REGISTRY is the thing that chases: `DllRegistry::resolve_in` follows
  a forwarder string's two halves (split at the LAST component dot --
  a dot-less, head-dot, or tail-dot string is a typed
  `MalformedForwarder` refusal, never resolved by guessing), re-enters
  on the named DLL's registered table, and caps the chain at 8 hops
  (`ForwarderDepthExceeded`). The chase's answers are typed the same
  way the import walk's are: an intermediate name with no registry
  entry is `MissingDllImport` (the name-vs-file ruling applies to
  forwarders exactly as it applies to import descriptors). A null EAT
  slot (0) answers ABSENT -- GetProcAddress semantics, never
  `load_base` itself, a tightening the old table walk silently
  violated and now cannot.
- **Ordinal imports resolve by the directory's OWN OrdinalBase.** The
  old loader refused `IMAGE_ORDINAL_FLAG64` imports outright. Now the
  ordinal import path mirrors the named path through the SAME chase:
  `OrdinalBase` is PARSED from the provider's export directory (never
  assumed), the thunk's ordinal names EAT index `ordinal -
  OrdinalBase`, and an ordinal beyond the EAT answers `Ok(None)` --
  which the walk turns into its per-symbol `MissingImport`, the exact
  policy an out-of-range NAME behind the ordinal table already had.
- **The fixtures are real on-disk compositions, checked, not
  simulated.** Because this host's GNU ld emits neither forwarded
  exports nor ordinal-form import thunks naturally (no dlltool on the
  host either), both structures are COMPOSED on top of otherwise
  ordinary toolchain output by `tools/patch_pe_forwarders.py` -- the
  same checked-patch discipline the weak-VERNEED artifact
  established: the patcher grows the .edata section into its own
  file-alignment padding (the extension is checked to touch no other
  section's bytes), grows the directory's declared Size to cover the
  composed strings (a forwarded string must live inside
  [dir_rva, dir_rva+dir_size) BY SPEC, so the growth is format-legal
  composition), rewrites the forwarder host's three EAT entries, and
  composes the CLIENT's crossmark OFT thunk into ordinal form with the
  ordinal computed from the EXISTING provider's tables (OrdinalBase
  read from the file, not assumed). It re-parses the files it wrote
  and refuses the build on any mismatch; a second run on a patched
  file refuses a double patch rather than recomposing. And the
  composition is not self-confessed output only: a plain `objdump
  -p` on the patched host prints its EAT entries as genuine
  `Forwarder RVA -- <string>` lines, which the layout gate pins.
  (An exploration-time bug was found and fixed the honest way: the
  patcher read OrdinalBase from offset +20 -- AddressOfFunctions' slot
  -- and the composed ordinal was visibly nonsense (0x3029); the
  offset was corrected to +16 and composed ordinal is pinned 2 =
  OrdinalBase(1) + crossmark's EAT index(1).)
- **The proof facts, every boot.** A second real provider (the
  "forwarder host", `userprog_pe_import_fwdprov.dll`, image base
  0x1c0000000000 -- private PML4 index 0x1c, same rule as the pair's
  bases) carries three names whose EAT entries are the composed
  forwarders; the client then imports a third symbol from it
  (`crossfn_fwd`) -- and the loader's chase lands the resolved slot
  on the PROVIDER's own crossfn address (verified against the
  named-form integration lookup), so the client's ring-3
  `call *__imp_crossfn_fwd(%rip)` executes the PROVIDER's code
  through the forwarder host's export entry. The client's crossmark
  import now walks in as the composed ORDINAL form; the walk's fact
  is labeled `ord2` and its value is checked to EQUAL the named
  crossmark answer -- the named-vs-ordinal cross-path equality
  (like the GNU==SysV hash answer-equality earlier rounds proved for
  ELF). Four typed negative controls, permanent, every boot: the
  self-cycling forwarder refuses `ForwarderDepthExceeded`; the
  unregistered-DLL forwarder refuses `MissingDllImport`; an
  out-of-range ordinal and an empty-table ordinal both answer
  typed-None, never a guess. The client's third report slot
  (`SLOT_C = SLOT_A + 16`, inside the one mapped page) is read back
  beside the other two, and the slot-constant gate pin now covers
  all three.
- **The gate catches every regression surface of both halves.**
  Provider-B's composed facts pinned from `objdump -p`'s own eyes
  (three names, each `Forwarder RVA --` string content); the client's
  three `__imp_*` IAT definitions and its double-descriptor import
  table; and the slot-constant cross-check extended with
  `SLOT_C == SLOT_A + 16` inside the 45-bit-class digit rule.
- **An honest post-publication note.** The FIRST increment shipped
  with sixteen one-line debug prints across the export parser, the
  section mapper/copier, and the data-directory reader -- this round
  caught and removed all of them (commits 696f41b and 1c46fa1), and
  the residue grep now matches the marker class `[dbg ` itself, not
  specific tags.

Deliberately still out of scope, same posture as before: delay-load
imports, resources, exception tables, and any Win32 API surface. What
this is: a real Windows-format import resolver that answers named,
ordinal, and forwarded exports the way GetProcAddress would. What this
is not: Windows applications. (Bound imports WERE in this list and are
closed by the 2026-10-20 increment below; delay-load imports were in
it too and are closed by the 2026-10-21 increment at the end of this
family; resources and exception tables are closed by the two
2026-10-24 cards.)

## Gap-closure increment (2026-10-20): bound imports -- the stamp is honored exactly as far as it can be VERIFIED

The Windows gap "a PE import resolver answers named/ordinal/forwarded
exports, but it cannot honor (or honorably discard) a DLL's pre-bound
IAT" now closes. `pe::resolve_imports` walks the real
`IMAGE_DIRECTORY_ENTRY_BOUND_IMPORT` directory (DataDirectory[11]) as
the Windows loader would, with this kernel's own verification posture
bolted on:

- **The bound ruling, per descriptor.** The bound directory's
  descriptors are parsed the bounded way (cap, terminator, per-entry
  module names are OFFSETS from the directory's own start bound within
  the directory's declared span) into `(module, stamp)` facts. For
  each import descriptor, the bound entry whose module name matches
  decides the path by comparing its stamp against the provider's OWN
  parsed `IMAGE_EXPORT_DIRECTORY.TimeDateStamp` -- read from the
  mapped image by the loader's OWN parser (`DllExportTable::
  time_date_stamp`, 0 for the empty table), never assumed:
  - **A matching stamp HONORS the pre-filled IAT -- after verifying.
    Each honored slot's pre-existing value is read and compared
    against what the registry freshly resolves for that import (named
    or ordinal form alike, forwarder chase included); equality passes
    and the slot is left UN-written (that is the point of binding:
    resolution was paid for at bind time, and the per-slot fact is
    recorded with `origin = BoundHonored`). A contradiction refuses
    the WHOLE load with the typed
    `PeError::BoundValueCorrupt(bound, fresh)` -- stamps claiming a
    fresh binding are verified, never trusted blindly: a wrong
    silently-honored call target is worse than a refused boot.
  - **Any other stamp DISCARDS.** The mismatching (stale) entry's
    pre-bound value is read, recorded verbatim as the slot's `prior`
    fact, NEVER trusted, and replaced by the fresh resolution --
    byte-identically what an unbound file would have gone through
    (the pe-import demo's stale slot goes through the FORWARDER
    CHASE and lands on the primary provider's own code, exactly as
    before).
  - **No bound entry (or no directory at all) is unchanged
    behavior:** fresh resolution with `origin = FreshResolution`; a
    directory the optional header does not even declare is ABSENT
    (`read_data_directory_absent_ok`), not malformed.
- **Inert entries are a fact, not a hazard.** A bound entry naming a
  DLL this image does not import never reaches a registry lookup: the
  demo composes `nobody.dll` deliberately and pins its ingest-and-
  ignore as a transcript fact (`bound_modules` carries all THREE
  parsed entries so the inert one's presence is checkable, not
  implicit).
- **The structural refusals are typed; the boot-controlled one is the
  stamp/verification one.** Unbounded descriptor arrays, names outside
  the directory's span, empty/overlong/un-terminatable names and a
  name offset of zero all refuse with the typed
  `MalformedBoundImport(&str)` and the load dies there -- same class
  of posture the shared text applies everywhere ("a malformed surface
  is worse than an absent one"), and same honest note as the forwarder
  round: these structural variants are code paths the boot
  transcript does NOT exercise (the bad-client control pins the
  SEMANTIC refusal, `BoundValueCorrupt`, which is the one bound
  imports exist for; the structural walk's every-failing-input game
  is a follow-up, not a silent assumption).
- **The composition is real on-disk bytes, checked (third patcher,
  same discipline).** GNU ld emits neither bound directories nor
  pre-filled IAT thunks naturally, so `tools/patch_pe_bound.py`
  composes, from the provider's OWN read file facts: the good
  client's 3-entry directory (the provider's real `.edata` stamp;
  `stamp + 1` for the forwarder host -- deliberately a stamp NO file
  carries; `nobody.dll` inert at 42) INSIDE the client's `.idata`
  in-block padding (VirtualSize grows, `SizeOfRawData` stays -- the
  first patcher's room rule) whose span is checked against EVERY
  other declared directory; the honored descriptor's two FirstThunk
  slots pre-filled with `PROVIDER_BASE + crossfn/crossmark EAT` (the
  values a real binder writes, verified by the KERNEL against a fresh
  resolution and then left un-written); the STALE descriptor's single
  slot carrying the OLD build's contradiction (crossmark's address in
  the crossfn-fwd slot -- any loader that trusted the stale stamp
  calls the wrong target; the ring-3 check proves the discard); and
  the BAD CLIENT (`userprog_pe_bound_bad.exe`, its own small `.s`,
  embedded via `include_bytes!`, entry never executed) carrying a
  matching-stamp entry whose single slot holds `0xdeadbeef00001000` --
  the stamp/IAT contradiction for negative control 5. Every
  composition re-parses what it wrote and refuses the build on a
  mismatch; a plain `objdump -x` prints the composed directories as
  genuine `Entry b ... Bound Import Directory` lines the gate pins.
- **The gate pin is a CROSS-FILE invariant, not a build stamp.** The
  stamps move every build (ld writes `time(2)`), so the gate pins the
  agreement, in file bytes: fresh entry stamp == provider .edata
  stamp; stale == fresh + 1; honored prefills == provider base + the
  EAT addresses read from the provider's own directory; the stale
  slot == the OLD-build contradiction; the bad client's single slot ==
  the patcher's WRONG constant; and the demo driver's `BAD_IMAGE_BASE`
  / `BOUND_WRONG_VALUE` consts agree with the file bytes and the
  patcher (three independent copies, one value -- the same
  never-drift rule the slot-constant pin families use).
- **The first boot caught the composition's own hole, and the fix
  went to the cause.** The initial composition left the stale
  descriptor's slots UNtouched -- and the demo's fresh assert failed
  with `left: 8360 (0x20a8)`, the toolchain's own UNBOUND hint/name
  RVA content. That is an honest and useful catch: with the slot
  carrying nothing pre-bound, "discard" would have been
  INDISTINGUISHABLE from ordinary fresh resolution and the stale
  claim would have proven nothing. The composition now pre-fills the
  stale slot with the old build's contradiction precisely so the
  discard is an observable fact (the pre-loader value is recorded as
  the `prior` field, and the ring-3 result proves the overwrite was
  the fresh chase, not the stale value).
- **Negative control 5, every boot:** the bad client is loaded next
  to the good one (own private PML4 index 0x40), its entry point is
  never entered, `resolve_imports` walks with the FULL registry (the
  refusal is SEMANTIC, not registry-shaped), and the specific
  mismatch must fire: the typed refusal names BOTH the contradicted
  bound value and the fresh resolution that contradicts it
  (`BoundValueCorrupt (0xdeadbeef00001000 != 0x180000001000)`). The
  old half's controls all keep firing (typed `MissingDllImport`,
  `MissingImport`, `ForwarderDepthExceeded`, typed-None ordinals) and
  the ring-3 self-test consumes all three fixture imports exactly as
  before -- the honored slots' values are the SAME verified addresses
  the fresh path would have written.
- **Verified:** full boot exit 0, 3/3 consecutive soak (281 OK
  lines), every gate line in the bound-composition family (Entry b
  lines for both clients, the cross-file stamp/prefill agreement
  block, the private-PML4-index rule for `BAD_IMAGE_BASE`), and the
  scratch-window rule extended so no ELF artifact may ever land in
  the bad client's fixed PML4-index-0x40 region.

Deliberately still out of scope, same posture as before: delay-load
imports, resources, exception tables, and any Win32 API surface. What
this is: a bound-import walk that honors a stamp exactly as far as the
stamp can be verified and discards it visibly otherwise. (The bound
descriptors' FORWARDER-REF half -- the per-descriptor
`NumberOfModuleForwarderRefs` sub-descriptors -- is now its own
increment, below.) Not Windows either. (Delay-load imports were still
in the out-of-scope list here and are closed by the 2026-10-21
increment below; resources and exception tables are closed by the two
2026-10-24 cards.)

## Gap-closure increment (2026-10-20, second of the day): bound FORWARDER-REFs -- the gate sees THROUGH the composed ref list

The bound-import ruling's own status note named the open half
honestly: a descriptor may declare
`NumberOfModuleForwarderRefs` same-shaped ref entries AFTER itself,
and the real loader consumes exactly that many (ReactOS's
`LdrpHandleOneNewFormatImportDescriptor` sets `FirstEntry =
ForwarderEntry` and jumps past them) -- so a descriptor whose OWN
stamp matches can still be stale THROUGH its refs. This closes that
half, with the conservative ruling and the same refuse/discard
discipline:- **Parsing (the boundary is a COUNT, not a heuristic):** the
  descriptor's third field is the real spec field, not a pad:
  `NumberOfModuleForwarderRefs` u16. The walk consumes exactly that
  many `IMAGE_BOUND_FORWARDER_REF` entries then (each: stamp u32 +
  module-name-offset u16 + Reserved u16; the ref's own count field is
  the Reserved half and is ignored), where the name offsets still
  resolve from the DIRECTORY's start and still must terminate inside
  the directory's declared span. Typed refusals stay typed: a ref
  without a name, a name outside the bounds, or the 32-entry cap
  (descriptors + refs TOGETHER) is `MalformedBoundImport`, never
  silent. The parsed surface changed shape accordingly:
  `tuple bound_modules` -> typed `BoundModule { name, stamp,
  forwarder_refs }`, and the loader returns the ref list verbatim.
- **The ruling (conservative, documented divergence).** Per
  descriptor, stale = own-stamp mismatch OR ANY ref whose module
  fails to load (registered stale/absent rules) OR a ref whose stamp
  mismatches its module's real parsed stamp. Stale-by-anything
  discards the WHOLE descriptor's pre-fill (coarse, per descriptor
  -- the Windows re-snap semantic; ReactOS's loop lets the LAST
  ref's check overwrite an earlier stale verdict, a quirk this
  kernel deliberately does not inherit) and resolves every slot
  fresh. A fresh-everything descriptor honors exactly as before --
  but the per-slot VALUE verification now runs the forwarder chase
  too, and a prefilled slot contradicting a verified chase target is
  the typed `BoundValueCorrupt` refusal.
- **The carrier pair, composing the decision boundary:** the bad
  client (`prog_pe_bound_bad.s`, recomposed THROUGH the forwarder
  host) carries a FRESH descriptor (the host's own .edata stamp)
  with ONE all-FRESH forwarder ref naming the primary provider, and
  its single IAT slot holds the composed contradiction. The honor
  gate is therefore gated ON purely by stamps -- so the slot
  verification must refuse it: every boot refuses with the typed
  `BoundValueCorrupt 0xdeadbeef00001000 != 0x180000001000`. The
  refstale carrier (`prog_pe_bound_refstale.s`) is the SAME
  composition except exactly ONE ref-stamp byte:   `provider + 1`,
  a stamp no file carries. The gate flips OFF by the ref alone: the
  same wrong slot is DISCARDED (typed `StaleRefDiscarded`) and
  resolved fresh through the same chase. One stamp byte decides
  refuse-vs-discard, and the loader proves it from real bytes each
  boot (include_bytes! both carriers).
- **The patcher grew the ref half** (`tools/patch_pe_bound.py`):
  `compose_bound_dir` now takes `[(name, stamp, refs)]` records,
  writes each descriptor followed by ITS ref entries, strictly
  sequential name area, dir[11] bounds = entries + refs + names +
  terminator, and re-parses every name offset/count/stamp written.
  `build.sh` links both carriers (private PML4-index-0x40 stays the
  bad client's region alone; the refstale carrier composes at the
  same RVA). The layout gate pins the new composition file-side:
  the bad client's fresh descriptor + one all-fresh ref + the wrong
  slot, and the refstale carrier's identical directory with exactly
  the ref stamp moved to provider+1.
- **Verified:** full boot exit 0, 3/3 consecutive soak, zero
  FAILs; negative control 5 (the fresh/gated refusal) and control 6
  (the one-byte discard) both fire from real composed bytes at every
  boot; all prior controls keep firing.

## Gap-closure increment (2026-10-20, third of the day): TWO-ref chains -- the count is proven, not assumed

The forwarder-ref round's own note named the next hole: chains of
more than one ref parsed-capable but unexercised. Closed with two
more carriers on the same composition family:

- **The carriers:** `ref2bad` composes the same single fresh
  descriptor naming the forwarder host -- but TWO forwarder refs: the
  primary provider and then the FORWARDER HOST ITSELF, each
  declaring its own module's real stamp, and the same wrong slot.
  Two DIFFERENT registered modules in one ref list means the per-ref
  lookup is real, not a one-module special case; the all-fresh
  surface gates the honor path ON purely by stamps, so the slot
  verification must refuse -- every boot refuses with the typed
  `BoundValueCorrupt 0xdeadbeef00001000 != 0x180000001000`, the
  chase proof that the walk consumed BOTH refs (a one-ref assumption
  would have misparsed the directory before any stamp check).
  `ref2stale` is the same composition except the SECOND ref's stamp
  is the host's own + 1: the gate flips OFF by a LATER ref alone,
  the same wrong slot is DISCARDED (typed `StaleRefDiscarded`) and
  resolved fresh through the same chase. One ref-stamp byte past
  the FIRST ref decides refuse-vs-discard across a two-ref chain.
- **The rules that were NOT violated:** the guard patcher composes
  both carriers under the same `{name, stamp, refs}` records path
  (re-verified by re-parse), the layout gate pins both files' dirs
  byte-exactly (entry count, ref order, both stamps, the wrong
  slot) and the demo's Ok-path control pins names/order/stamps from
  mapped bytes; new fixed bases 0x5000_0000_0000 / 0x6000_0000_0000
  are private PML4 indexes 0xA0/0xC0 and scratch-window-protected.
  A proven parse boundary: refusals discard the parsed surface by
  design, so the refusal-path asserts only the typed pair and the
  parse facts come from the Ok-path carrier + the gate -- no
  recovery-through-error was added.
- **Verified:** full boot exit 0, 3/3 consecutive soak, zero
  FAILs; negative control 7 (two-ref all-fresh refusal) and control
  8 (second-ref-stale discard) both fire from real composed bytes at
  every boot; controls 1-6 all keep firing.

Still deliberately out, same posture: delay-load imports, ref
chains longer than TWO refs (the count-driven walk bounds and
consumes any number, but no carrier composes three), structural
`MalformedBoundImport` variants are code-verified but not
boot-controlled, and Win32 API. The one-ref round's ruling stands
unchanged for single-ref descriptors; every existing fact, gate
line, and carrier composes identically. (Delay-load imports WERE in
this "still deliberately out" list and are closed by the 2026-10-21
increment immediately below.)

## Gap-closure increment (2026-10-21): delay-load imports -- resolved EAGERLY, because there is no helper to defer to

`pe.rs`'s last listed import gap (every earlier increment's
"still deliberately out: delay-load imports") is closed with the
same posture as the rest of the file: the real directory, walked
strictly, resolved through the SAME registry, and every
contradiction refused with its own typed name.

- **The walk** (`pe::resolve_delay_imports`, `DelayImportResolution`):
  `IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT` (dir[13], absent-OK like the
  other optional directories), descriptors of the genuine 8-DWORD
  `ImgDelayDescr` (`grAttrs, rvaDLLName, rvaHmod, rvaIAT, rvaINT,
  rvaBoundIAT, rvaUnloadIAT, dwTimeStamp`), all-zero terminator, the
  descriptor array capped at 16 and each IAT/INT array at 64 -- an
  unbounded surface is a `MalformedDelayImport`, never a longer
  loop. `grAttrs` must be `dlattrRva` (0x1, the only documented
  attribute); any other bit is refused, as the Windows helper itself
  treats bad attributes as unrecoverable. Name and IAT are required.
  The pre-VC7 VA-form descriptor is decoded as `VA - preferred
  ImageBase` and REFUSED in a relocated image (its baked VAs are
  stale). The INT is the name source when present and must equal the
  IAT entry-for-entry through both terminators: the unbound pre-state
  is what this walk resolves, so an already-resolved or altered IAT
  is a malformed surface, not a thing to guess about.
- **The eager-resolution ruling, stated as the divergence it is:**
  Windows defers each delay import to the client's first call through
  `__delayLoadHelper2` -- a ring-3 fault protocol this kernel does not
  host. So the kernel resolves the delay IAT AT LOAD TIME through the
  same registry the ordinary walk uses (named and ordinal shapes,
  forwarder chase included), WRITES each slot (an eager resolve is a
  fresh resolution, never a bound honor), and stores the provider's
  MAPPED base as the module handle at `rvaHmod` -- after which the
  client's own code loads through the slots exactly as if it had been
  bound at link time. Facts surface in the standard `ResolvedImport`
  shape with a new `ImportOrigin::DelayResolved`, so the demo can pin
  the origin as well as the values. `rvaBoundIAT`/`rvaUnloadIAT` are
  tolerated (Windows unbind/unload bookkeeping, not resolution); a
  nonzero `dwTimeStamp` (the old-.BIND marker) is REFUSED -- a
  binding guarantee the kernel cannot verify is neither honored nor
  silently ignored.
- **Two typed refusals, not one:** a delay descriptor naming an
  unregistered DLL is `MissingDelayDllImport` (deliberately distinct
  from the ordinary walk's `MissingDllImport`, so the refusal names
  WHICH table failed), and every structural violation is
  `MalformedDelayImport(&'static str)` with the reason in the
  payload.
- **The carriers are composed, checked, and re-parsed**
  (`tools/patch_pe_delay.py` + `userprog/prog_pe_delay.s`, the same
  discipline `patch_pe_bound.py`/`patch_pe_forwarders.py`/
  `patch_weak_verneed.py` established): TEN toolchain-raw
  no-import PE32+ clients get their `IMAGE_DELAYLOAD_DESCRIPTOR`
  surfaces composed into the `.idata` section's own in-block padding
  (VirtualSize grows, SizeOfRawData stays; the three cap-boundary
  carriers instead grow SizeOfRawData under the checked extension
  described below), then the result is re-parsed and the build is
  refused unless the on-disk bytes are the composed bytes -- `delaygood` (named `crossfn` + ordinal-form
  `crossmark`, both descriptors fresh, dwTimeStamp 0), `delaybad`
  (module `nowhere.dll`), `delaymut` (the provider's name with
  `dwTimeStamp = provider .edata stamp + 1`) -- plus the three
  STRUCTURAL carriers, each the canonical composition with exactly one
  property broken: `delayattrs` (`grAttrs = 0x3`, dlattrRva plus an
  undefined bit), `delaymis` (IAT[0] carries an ordinal thunk while
  INT[0] names crossfn: the broken unbound pre-state) and `delayva`
  (`grAttrs = 0`, the pre-VC7 VA form, with the fields left as the
  RVAs the canonical layout computes -- every one far below the
  image's own ImageBase) -- and a FOURTH, `delayvabias`: the genuine
  VA-form carrier, linked at `0x40_0000` so its 32-bit VA fields fit,
  and loaded away from that base (see the relocated branch below). The
  layout gate pins
  each file's dir[13] independently: the span sits exactly at
  `.idata` vaddr + (grown vsize - dir size), grAttrs is
  `dlattrRva`, bound/unload RVAs are zero, the name is exact, IAT ==
  INT entry-for-entry with both terminators, `dwTimeStamp` is the
  exact composed value, the span equals the closed-form composed
  length, and the file's ImageBase equals the demo's constant at a
  private PML4 index -- with `grAttrs` and the one composed
  contradiction pinned per carrier (the gate asserts `delaymis`'s IAT
  IS the ordinal thunk and its INT IS the named blob, so that file
  can never start refusing for a different reason).
- **The first seven boot controls through the real bytes**
  (`pe_import_demo`, in two of its three non-inlined frames -- each
  family got its own so its mapped images' locals never touch the boot
  frame's measured peak; the cap family is the third, below): control
  9 is the positive
  half -- both delay imports eagerly resolved to the provider's own
  `crossfn`/`crossmark` addresses, the priors read back as the
  composed UNBOUND thunks (the ordinal slot's prior is
  `ORDINAL_FLAG64 | ord2`, the named slot's a hint/name RVA, never an
  absolute pointer), both slots WRITTEN and read back, and the module
  handle == the provider's mapped base -- the eager ruling's answers
  are the SAME answers the normal walk gives. Controls 10 and 11 are
  the refusals: `nowhere.dll` -> `MissingDelayDllImport`, and
  `dwTimeStamp = stamp + 1` -> the typed
  `MalformedDelayImport("bound delay-load descriptors (dwTimeStamp !=
  0) are out of scope -- refused, not ignored")`.
- **The STRUCTURAL refusals are boot facts too, not code claims**
  (controls 12-14): `delayattrs` -> "unknown delay descriptor grAttrs
  bits"; `delaymis` -> "delay INT entry does not match its IAT
  entry"; `delayva` -> "delay VA-form descriptor field below the
  preferred image base". Each arm matches the EXACT payload string,
  not merely `Err(MalformedDelayImport(_))`: a carrier that broke for
  an earlier, different reason fails the boot instead of passing as
  the intended refusal. That distinction is the whole point of the
  family -- these three reasons were code-verified only until a
  composed file made the kernel say them at boot.
- **The relocated VA-form branch, and the DEAD CODE the attempt
  found** (control 15, `delayvabias`): a descriptor field is 32 bits,
  so a genuine pre-VC7 VA-form descriptor exists only for an image
  below 4 GiB -- this carrier is linked at the classic `0x40_0000`
  and LOADED at `0x4600_0000_0000` (never at its own base: index 0 is
  the shared identity subtree). Composing it exposed a real bug in the
  walk's VA decode: the bounds test compared the VA ITSELF against
  `max_rva`, which no real VA can pass, so every VA-form field was
  refused as "below the preferred image base" and -- worse -- the
  relocated-image branch BELOW it could never be reached at all. Fixed
  at cause: relocation is now a DESCRIPTOR-level question, decided once
  before any field is touched (a relocated image's baked VAs are all
  stale by the same delta), and the decode checks the DERIVED RVA
  against the mapped footprint, with its own message ("beyond the
  image's mapped footprint"). The demo pins the carrier's preconditions
  from the FILE (its own ImageBase is the low one; the load base is not
  it) rather than trusting the link line, and the gate pins both bases
  plus the fact that all four address fields really are VAs of the low
  base.
- **The caps are EXCLUSIVE, and both sides are composed files now**
  (`delaymany`, `delaythunks`, `delayfull`): the walk reads a
  descriptor's / thunk's zero terminator BEFORE it consults the cap, so
  exactly 16 descriptors and exactly 64 thunk entries are the LEGAL
  maxima. That ordering is what makes the caps exclusive rather than
  merely bounded -- and it is a property a refusal CANNOT evidence on
  its own, because a cap checked before the terminator read would
  refuse the 17th descriptor identically while wrongly refusing a
  legal 16. So both sides are composed: `delaymany` carries 17
  descriptors (each naming the provider with one import),
  `delaythunks` one descriptor with 65 thunk entries, and `delayfull`
  the two legal maxima AT ONCE -- exactly 16 descriptors whose FIRST
  carries exactly 64 entries (79 imports) -- which the walk must
  ACCEPT. The three need more room than `.idata`'s FileAlignment-bound
  padding can hold, so `patch_pe_delay.py` EXTENDS the section's raw
  span under checks: `.idata` must be the last section by raw
  placement, every trailing byte must be exactly the COFF symbol table
  `ld` appended (stripped explicitly -- pointer and count zeroed -- not
  silently overwritten), and the new span is FileAlignment-aligned. The
  composition itself is generalized to N descriptors / N thunks, with
  the single-descriptor layout byte-identical to what the earlier seven
  carriers were built with (verified: all seven rebuilt files are
  byte-for-byte what they were before the generalization). **The
  extension's checks were probe-verified, not asserted** (both probes on
  throwaway copies): appending 32 unexplained bytes after the COFF
  symbol table makes the patcher exit 1 with `FAIL: delay composition:
  bytes [0xea3..0xec3) after the COFF symbol table are unexplained --
  refusing to extend over them`, and bending one section header to claim
  raw bytes after `.idata` makes it exit 1 with `FAIL: delay
  composition: section '.text' holds raw bytes AFTER .idata -- extending
  .idata's raw span would overwrite them`.
- **The cap boundary is a boot fact on BOTH sides** (controls 16-18,
  own non-inlined frame): control 16 matches `MalformedDelayImport(
  "delay descriptor array unbounded")` AND proves how far the walk got
  -- descriptors 0-15 are fully resolved (their IAT slots hold the
  provider's mapped `crossfn`, their handle slots the provider's base)
  while the 17th descriptor's slot still equals its composed INT entry
  and its handle slot is untouched; control 17 does the same for the
  65th thunk of a one-descriptor carrier (64 slots resolved, entry 64
  still the unbound thunk); control 18 is the legal side -- exactly 16
  descriptors with 64 thunks in the first are ACCEPTED, 79 facts
  resolved, every slot and all 16 handle slots read back, and the
  composed IAT terminator right after the 64th entry is what stopped
  the walk. The slot-state reads are derived from the MAPPED image's
  own descriptor table (dir[13], then each descriptor's `rvaIAT`),
  never from a hardcoded composed offset.
- **Two real bugs, both fixed at cause.** (1) The delay section's
  locals pushed the boot function's kernel-frame peak past the guard
  page, and the first boots double-faulted SEVERAL CONTROLS EARLIER
  than the delay code runs with the same stack pointer every time --
  the fix is the chain's established rule, non-overlapping locals in
  their own non-inlined frame, not a bigger stack. (2) The
  BAD/MUT carriers were first linked at `0x8000_0000_0000` -- **bit
  47 exactly**, i.e. in 4-level x86-64's NON-CANONICAL hole (bit 47
  set, bits 63:48 clear), so every byte read of those images #GP(0)'d
  and the boot hung before control 10's refusal could print. The
  prove-it step that named the cause in one boot was QEMU's CR2,
  `0xffff800000000000`: the tentatively-canonical pointer the #GP's
  own side effect canonicalizes to. The carriers now link at
  `0x4100_0000_0000` / `0x4200_0000_0000` (free, private, canonical
  subtrees), and the rule is a GATE rule rather than a comment: every
  PE carrier's ImageBase is refused at build time unless it is below
  `2^47` -- a load image may never be linked into the canonical hole
  again.
- **The new rule was probe-verified, not asserted:** re-linking the
  `delaybad` carrier at `--image-base=0x800000000000` WITH the demo's
  `DELAYBAD_IMAGE_BASE` const moved to the same value (so the
  const-vs-file equality check passes and only the canonicality
  question is left) makes `./build.sh` exit 1 with
  `FAIL: delaybad ImageBase 0x800000000000 sits in the 4-level x86-64
  NON-CANONICAL hole (bit 47 set, bits 63:48 clear) -- every access
  to the image #GP(0)s; link it below 2^47`. Both edits were then
  reverted to the committed state and the build + boot re-run green,
  so the gate is proven to REFUSE the class it exists for rather than
  merely to pass the good files.
- **Verified:** `./build.sh` exit 0 (all nineteen PE link products + the
  delay-composition block for ten carriers + the canonical-base
  rule), `tools/qemu_boot_test.sh` exit 0 (`PASS: kernel reached
  BootPhase::Ready and exited cleanly`), controls 1-18 all `OK` in
  three consecutive boots (276 OK lines each), and zero
  `[dbg`/`TEMP BISECT` residue in `src/`.

## Gap-closure increment (2026-10-22): the native UEFI boot path boots the FULL chain end-to-end, and the loader write contract is now CR0.WP-proof

The Phase 10 UEFI stub (`uefi/`) is no longer a bring-up scaffold: a
kernel booted natively through OVMF (GRUB never involved) now prints
the same complete demo-chain transcript as the Multiboot2/BIOS boot --
identical `OK` counts (244) in both boots, same final
`phase 1+2+3+4+ring3-elf-isolation-poc+guard-pages complete` banner,
zero faults. Getting there surfaced (and fixed) real divergences
between the two boot environments; four are worth their bullets:

- **Every boot path now ends in the SAME CPU state the loader assumes.**
  boot.s's `uefi_entry` loads null data selectors before calling
  `kernel_main`: OVMF leaves SS/DS/ES selector 0x30 live, which -- after
  the kernel's own GDT is loaded (where 0x30 is the TSS descriptor) --
  general-protects on every IRET the exception handlers execute, i.e.
  the whole self-test chain, at the first interrupt return. The BIOS
  path never built segments at all, so the same latent bug was
  invisible there.
- **The record-to-memory boot handoff is spec-complete:** the stub
  synthesizes a Multiboot2 info structure (memory map, vendor tag,
  ACPI RSDP copied from the EFI configuration table) and passes the
  boot-loader magic the spec fixes, so `src/multiboot.rs` runs unmodified
  on both paths. The bugs this exposed, each in the stub, in order:
  the ACPI-20 configuration-table GUID constant was typed wrong (the
  kernel's RSDP validation then correctly rejected everything;
  the constant now matches the spec value `8868e871-e4f1-11d3-bc22-0080c73c8881`
  and the copy path additionally validates the `RSD PTR ` signature
  before comitting), EDK2 returns `desc_size = 48` for every
  `EFI_MEMORY_DESCRIPTOR` (spec minimum 40 -- striding by 40 silently
  deserialized garbage from the second entry on), the pre-EBS
  `GetMemoryMap` refetch legitimately returns `EFI_BUFFER_TOO_SMALL`
  (the stub's own pool allocations grow the map between acquire and
  refetch; the retry now re-grows the buffer instead of assuming a
  fixed capacity), and the stub's OWN boot pages (its page tables, the
  handoff page, the loaded kernel image, the synthesized MBI) were
  EfiLoaderData i.e. "conventional" in the synthesized map -- the
  kernel's frame allocator handed out its own page-table pages as data
  frames and tore the identity mapping down mid-bring-up. Those pages
  are carved out as explicit reserved entries before synthesis now.
- **THE CR0.WP BOOT-ENVIRONMENT FINDING (the reason this increment
  touches the loaders too):** a legacy BIOS boot leaves CR0.WP CLEAR
  (boot.s only sets PG; GRUB does not set it either), while OVMF hands
  over with WP SET (verified at fault time: CR0 = `0x80010033`). With
  WP clear, a RING-0 write into a read-only PTE silently SUCCEEDS; with
  WP set it protection-faults. Every loader in this kernel mapped its
  segments honoring the file's W permission, then wrote into them
  anyway (zero-fill + copy for `p_memsz` pages, stub byte emission into
  the lazy trampoline/thunk pages, header pages) -- so every boot on
  BIOS was silently over-claiming a read-only mapping's protection.
  The first UEFI boot surfaced this as a cascade of
  `PROTECTION_VIOLATION|CAUSED_BY_WRITE` faults at exactly those writes
  (yield-program text page; then, one fixed, the full-demo's lazy
  trampoline page, and so on down the chain).
- **The loaders' contract is now explicit and WP-independent, the way
  real loaders work: map writable for the load/emit window, then
  revoke to exactly the file-requested flags.** `experimental::elf` maps
  every PT_LOAD with the write grant, zero-fills/copies, and then
  removes WRITABLE from every segment the file did not declare
  PF_W (`SegmentProtectFailed` on a failed revoke -- a read-only
  segment left writable is a refused load, the same posture as the
  RELRO machinery); the synthetic lazy trampoline page and the
  own-.plt thunk page get the same grant/revoke around their emission.
  `experimental::pe` does the same for the header pages (always
  loader-internal) and for sections without `IMAGE_SCN_MEM_WRITE`
  (`SectionProtectFailed`). Final page states are byte-for-byte what
  the files request, so every pre-existing enforcement self-test (RELRO
  read-only checks, `ImportSectionNotWritable` refusals, W^X controls)
  is unchanged and still green on BOTH paths.
- **The kernel sets CR0.WP ITSELF as a documented boot contract** (right
  after `paging::init`, read back asserted -- see `kernel_main`'s
  `paging:` serial lines): no boot path"s firmware state decides
  whether supervisor writes honor PTE RO. A future caller that skips
  the loaders' grant window now faults loudly on BIOS too, instead of
  the two boot paths diverging in behaviour again.
- **Verified:** `./build.sh` exit 0; the GRUB/Multiboot2 dev boot and
  the native OVMF/UEFI boot each print 244 `OK` lines with zero
  `FAILED`/`EXCEPTION`/panic and the same final banner;
  `tools/qemu_boot_test.sh` exit 0 (`PASS: kernel reached
  BootPhase::Ready and exited cleanly`) -- run after the WP positive
  control was added, so the enforcement contract is itself part of what
  the boot tests prove.

## Gap-closure increment (2026-10-23): both boot gates are CI scripts, and their first back-to-back run is recorded below with BOTH transcripts

The 2026-10-22 increment's boots were verified interactively. This
increment turns both boot contracts into what CI runs, then executes
them back-to-back on one machine state and records the full guest
transcripts at the bottom of this section, so the "same chain on both
paths" claim is checkable line-for-line rather than asserted.

- **`tools/qemu_uefi_boot_test.sh` got a CI home** (`.github/workflows/ci.yml`,
  new `kernel-boot` job): the job installs the boot-emulation toolchain
  (qemu, mtools, grub-pc-bin+xorriso, the distro `ovmf` package --
  firmware the repo deliberately does NOT track, see .gitignore), feeds
  `uefi/tools/` from `/usr/share/OVMF/*_4M.fd`, and runs BOTH gates:
  `qemu_boot_test.sh` (GRUB/Multiboot2 BIOS) then
  `qemu_uefi_boot_test.sh` (native OVMF stub handoff). Each gate's only
  pass condition is the isa-debug-exit contract: the guest reaches its
  final state and exits with host status 33; 124 is printed-labelled
  timeout, anything else a loud FAIL with the guest serial log printed.
- **The back-to-back run (record):** BIOS gate first, UEFI gate
  immediately after, same workspace, nothing rebuilt between except by
  the scripts themselves. Both exits 0. The gate wrappers are excluded
  from the blocks below (only guest serial output is quoted);
  OVMF's console-reset ANSI escape sequences are elided from the UEFI
  block -- every kernel line is verbatim otherwise.

| Gate | Contract proven | Guest lines | `-- OK` fact lines | Exit |
|---|---|---|---|---|
| `qemu_boot_test.sh` (BIOS) | boots through GRUB/Multiboot2, full demo chain, final banner, clean walk-off exit | 355 | 242 | 33 -> 0 PASS |
| `qemu_uefi_boot_test.sh` (UEFI) | same contract with GRUB imagined OUT: OVMF boots `BOOTX64.EFI`, the stub loads the kernel, synthesizes MBI, EBS, CR3 switch, `uefi_entry` | 369 | 241 | 33 -> 0 PASS |

- **What the two transcripts do and do not share (from the actual
  diff, catalogued once):** the UEFI transcript has, above the kernel's
  first line, the OVMF boot-choice lines and the stub's own 14 lines
  (SecureBoot=absent, kernel image loaded from the stub's own bytes,
  memory map acquired, RSDP rev=2.0, Multiboot2 info synthesized with
  the vendor tag, ACPI-reclaim carve-out check, EBS, CR3 switched,
  `uefi_entry` jumped). The kernel-side differences are exactly the
  boot-environment facts each transcript HONESTLY reports: the memory
  map's usable-frame count (GRUB reports 127 MiB usable; the UEFI map
  243 MiB), ACPI (RSDP v0/RSDT root, 4 tables under BIOS; RSDP v2/XSDT
  root, 6 tables under OVMF, WITH a real ACPI-reclaimable region that
  the reclaim check then actually releases -- under BIOS the same
  probe reports no such region and proves the release contract with
  its synthetic control instead), the ATA self-test (a disk sits on
  the BIOS run's secondary channel; none under this OVMF run), and
  nondeterminism that is nondeterminism by design (RTC wall-clock
  seconds, RTC/timer tick counts, ring-3 preemption spin counters).
  Everything else -- the whole scheduler/IPC/lifecycle/isolation/DEX/
  teardown/ring-3/dyn-linker/PE chain, all 242 vs 241 `-- OK`
  fact lines, zero FAULT/FAILED/panic except the guard-demo's intended
  overflow catch and the deps-demo line about demonstrating the panic
  path -- is line-identical between the two boot paths, ending in the
  same `nyrqis kernel: phase 1+2+3+4+ring3-elf-isolation-poc+guard-pages
  complete` banner. Two different firmware worlds, one transcript.
- **The claim is now MACHINE-CHECKED, not just recorded**
  (`tools/qemu_boot_parity_diff.sh`, wired as the `kernel-boot` job's
  third gate): the script re-runs BOTH gates and diffs the two guest
  transcripts after stripping only a DOCUMENTED allowlist of honest
  boot-environment lines -- the UEFI stub section, the memory-map
  usable-frame counts, the ACPI shape lines, the ATA
  device-presence line, and the by-design nondeterminism (RTC
  seconds/ticks, ring-3 preemption spin counters). Everything else
  must be line-identical, and the first local run PASSES exactly so:
  BIOS 342 chain lines == UEFI 342 chain lines, residual diff empty.
  A residual delta outside the allowlist is a printed FAIL, so a
  future loader change that makes the two boot paths diverge
  ring-the-parity-bell instead of silently re-dividing behaviour.
- The full transcripts follow. First the BIOS gate's guest output:

```
nyrqis kernel: phase 1 bring-up
  gdt: loaded (kernel code segment + tss)
  idt: loaded (divide-error, invalid-opcode, gpf, page-fault, double-fault)
  double-fault ist stack: index 0
  multiboot: memory map parsed, 32639 usable 4KiB frames (127 MiB)
  acpi: RSDP v0 validated (RSDT root table), 4 tables enumerated and checksum-verified
  acpi reclaim: platform reports no AcpiReclaimable region; synthetic control on reserved 0xf0000..0x100000: usable before=false (expected false), after release=true (expected true), after restoring=false (expected false) -- OK
  paging: CR0.WP set by the kernel (supervisor writes honor PTE RO on both boot paths -- documented boot contract, not firmware state)
  paging: dynamic mapper live (CR3 read back, OffsetPageTable constructed), NX enabled (EFER.NXE)
  heap: mapped 256 pages (1024 KiB) at 0x10000000000, global allocator live
  heap self-test: Vec<u32> of 100 elements allocated and summed correctly (5050)
  frames allocated so far: 259
  memory tlb-demo: mapped (frame Some(PhysAddr(0x33d000))), unmapped (translate -> None), remapped (frame Some(PhysAddr(0x341000))); re-read 0xbbbbbbbbbbbbbbbb (expected 0xbbbbbbbbbbbbbbbb)
  memory tlb-demo self-test: present->absent->present OK, backing frame genuinely changed OK, remap read back new bytes OK -- OK
  pic: remapped (IRQ0-7 -> 0x20-0x27, IRQ8-15 -> 0x28-0x2F), timer unmasked
  rtc: periodic interrupt armed at 1024 Hz, IRQ8 + cascade IRQ2 unmasked
  keyboard: 8042 keyboard interrupt (IRQ1, master PIC) enabled and unmasked
  interrupts: enabled (sti)
  timer self-test: 20 real hardware timer interrupts observed and handled
  rtc self-test: 4 additional RTC periodic interrupts observed and handled after a snapshot (1092 total; IRQ8, via the slave-PIC cascade)
  rtc wall-clock self-test: first read Ok(RtcTime { second: 0, minute: 8, hour: 7 }) (decoded via the hardware's actual mode bits, fields in valid ranges) -- OK
  rtc wall-clock self-test: bounded-UIP second read Ok(RtcTime { second: 0, minute: 8, hour: 7 }) completed without hanging -- OK
  rtc wall-clock self-test: second read does not move the clock backwards beyond a tolerated skew -- OK
  rtc wall-clock self-test: 8 periodic tick(s) observed between the two wall-clock reads (the periodic line and the wall-clock registers are both alive) -- OK
  rtc wall-clock self-test: wall-clock reads decode via the hardware's reported modes and order monotonically -- OK
  keyboard self-test: injected scancode 0x1e via the 8042 output-buffer command (0xD2), 1 interrupt(s) handled, handler read back 0x1e -- OK
  ata self-test: secondary channel master detected, IDENTIFY (0xa1) completed with status 0x58 after 2 interrupt(s) on IRQ15 -- OK, data-ready
nyrqis kernel: phase 1+2+3-prep complete, boot phase = 7 (Ready)
  experimental scheduler: 4 tasks registered (lifecycle-enforcing, NPS-002 §5)
  experimental ipc: 4 mailboxes initialized (arbiter-checked, NPS-003 §5.4)
    [experimental task A] activation 1: sent IPC message to task B
    [experimental task B] activation 1: received IPC message {sender: 1, seq: 1} -- OK
    [experimental task J] activation 1: terminating per NPS-002 §5.5/§5.6 (RUNNING -> TERMINATING -> TERMINATED, then dispatch away)
    [experimental task A] activation 2: sent IPC message to task B
    [experimental task B] activation 2: received IPC message {sender: 1, seq: 2} -- OK
    [experimental task A] activation 3: sent IPC message to task B
    [experimental task B] activation 3: received IPC message {sender: 1, seq: 3} -- OK
    [experimental task A] activation 4: sent IPC message to task B
    [experimental task B] activation 4: received IPC message {sender: 1, seq: 4} -- OK
    [experimental task A] activation 5: sent IPC message to task B
    [experimental task B] activation 5: received IPC message {sender: 1, seq: 5} -- OK
    [experimental task A] activation 6: sent IPC message to task B
    [experimental task B] activation 6: received IPC message {sender: 1, seq: 6} -- OK
  experimental scheduler termination proof: task J walked RUNNING -> TERMINATING -> TERMINATED and was never re-dispatched (ran exactly 1 time) -- OK
  experimental scheduler self-test: 6 round trips completed, task A ran 6 times, task B ran 6 times
  experimental ipc self-test: 6 messages sent task A -> task B and received correctly, 0 mismatches
  experimental scheduler lifecycle self-test: all 6 transitions attempted from Created matched the edge table (accepted iff legal) -- OK
  experimental scheduler lifecycle self-test: all 6 transitions attempted from Ready matched the edge table (accepted iff legal) -- OK
  experimental scheduler lifecycle self-test: all 6 transitions attempted from Running matched the edge table (accepted iff legal) -- OK
  experimental scheduler lifecycle self-test: all 6 transitions attempted from Blocked matched the edge table (accepted iff legal) -- OK
  experimental scheduler lifecycle self-test: all 6 transitions attempted from Terminating matched the edge table (accepted iff legal) -- OK
  experimental scheduler lifecycle self-test: all 6 transitions attempted from Terminated matched the edge table (accepted iff legal) -- OK
  experimental scheduler lifecycle self-test: 36/36 from-to pairs verified (9 legal accepts, 27 illegal refusals, counter ticked exactly once per refusal) -- OK
  experimental scheduler termination self-test: 1 terminated task(s) (reached via the legal path), TERMINATED unreachable directly from CREATED/READY, remaining tasks active -- OK
  experimental ipc arbiter self-test: honest send from the running task -- OK (queued)
  experimental ipc arbiter self-test: forged sender (claims 1, running 0) refused with NotRunningTask -- OK
  experimental ipc revocation self-test: revoke_sender(scratch, 0) newly-revoked -- OK
  experimental ipc revocation self-test: send after revocation refused with Revoked -- OK
  experimental ipc revocation self-test: message queued before revocation still receivable after it -- OK
  experimental ipc revocation self-test: re-revoking an already-revoked sender is a no-op returning false -- OK
    [experimental task A] activation 7: sent IPC message to task B
    [experimental task B] activation 7: received IPC message {sender: 1, seq: 7} -- OK
    [experimental task A] activation 8: sent IPC message to task B
    [experimental task B] activation 8: received IPC message {sender: 1, seq: 8} -- OK
  experimental ipc blocking-receive self-test: bounded receive on an empty mailbox returned ReceiveTimedOut (typed, never an indefinite block) -- OK
  experimental ipc blocking-receive self-test: bounded receive delivered the message sent before it (Ok(IpcMessage { sender: 0, seq: 7 })) -- OK
  experimental ipc self-test: arbiter + revocation + blocking-receive contract checks -- OK
  experimental isolated-tasks: 6 total tasks in round-robin (main, A, B, C[own cr3 0x350000], D[own cr3 0x35a000])
    [experimental task A] activation 9: sent IPC message to task B
    [experimental task B] activation 9: received IPC message {sender: 1, seq: 9} -- OK
    [experimental task A] activation 10: sent IPC message to task B
    [experimental task B] activation 10: received IPC message {sender: 1, seq: 10} -- OK
    [experimental task A] activation 11: sent IPC message to task B
    [experimental task B] activation 11: received IPC message {sender: 1, seq: 11} -- OK
    [experimental task A] activation 12: sent IPC message to task B
    [experimental task B] activation 12: received IPC message {sender: 1, seq: 12} -- OK
    [experimental task A] activation 13: sent IPC message to task B
    [experimental task B] activation 13: received IPC message {sender: 1, seq: 13} -- OK
    [experimental task A] activation 14: sent IPC message to task B
    [experimental task B] activation 14: received IPC message {sender: 1, seq: 14} -- OK
    [experimental task A] activation 15: sent IPC message to task B
    [experimental task B] activation 15: received IPC message {sender: 1, seq: 15} -- OK
    [experimental task A] activation 16: sent IPC message to task B
    [experimental task B] activation 16: received IPC message {sender: 1, seq: 16} -- OK
    [experimental task A] activation 17: sent IPC message to task B
    [experimental task B] activation 17: received IPC message {sender: 1, seq: 17} -- OK
    [experimental task A] activation 18: sent IPC message to task B
    [experimental task B] activation 18: received IPC message {sender: 1, seq: 18} -- OK
    [experimental task A] activation 19: sent IPC message to task B
    [experimental task B] activation 19: received IPC message {sender: 1, seq: 19} -- OK
    [experimental task A] activation 20: sent IPC message to task B
    [experimental task B] activation 20: received IPC message {sender: 1, seq: 20} -- OK
    [experimental task A] activation 21: sent IPC message to task B
    [experimental task B] activation 21: received IPC message {sender: 1, seq: 21} -- OK
    [experimental task A] activation 22: sent IPC message to task B
    [experimental task B] activation 22: received IPC message {sender: 1, seq: 22} -- OK
    [experimental task A] activation 23: sent IPC message to task B
    [experimental task B] activation 23: received IPC message {sender: 1, seq: 23} -- OK
    [experimental task A] activation 24: sent IPC message to task B
    [experimental task B] activation 24: received IPC message {sender: 1, seq: 24} -- OK
    [experimental task A] activation 25: sent IPC message to task B
    [experimental task B] activation 25: received IPC message {sender: 1, seq: 25} -- OK
    [experimental task A] activation 26: sent IPC message to task B
    [experimental task B] activation 26: received IPC message {sender: 1, seq: 26} -- OK
    [experimental task A] activation 27: sent IPC message to task B
    [experimental task B] activation 27: received IPC message {sender: 1, seq: 27} -- OK
    [experimental task A] activation 28: sent IPC message to task B
    [experimental task B] activation 28: received IPC message {sender: 1, seq: 28} -- OK
    [experimental task A] activation 29: sent IPC message to task B
    [experimental task B] activation 29: received IPC message {sender: 1, seq: 29} -- OK
    [experimental task A] activation 30: sent IPC message to task B
    [experimental task B] activation 30: received IPC message {sender: 1, seq: 30} -- OK
    [experimental task A] activation 31: sent IPC message to task B
    [experimental task B] activation 31: received IPC message {sender: 1, seq: 31} -- OK
    [experimental task A] activation 32: sent IPC message to task B
    [experimental task B] activation 32: received IPC message {sender: 1, seq: 32} -- OK
    [experimental task A] activation 33: sent IPC message to task B
    [experimental task B] activation 33: received IPC message {sender: 1, seq: 33} -- OK
    [experimental task A] activation 34: sent IPC message to task B
    [experimental task B] activation 34: received IPC message {sender: 1, seq: 34} -- OK
    [experimental task A] activation 35: sent IPC message to task B
    [experimental task B] activation 35: received IPC message {sender: 1, seq: 35} -- OK
    [experimental task A] activation 36: sent IPC message to task B
    [experimental task B] activation 36: received IPC message {sender: 1, seq: 36} -- OK
    [experimental task A] activation 37: sent IPC message to task B
    [experimental task B] activation 37: received IPC message {sender: 1, seq: 37} -- OK
    [experimental task A] activation 38: sent IPC message to task B
    [experimental task B] activation 38: received IPC message {sender: 1, seq: 38} -- OK
    [experimental task A] activation 39: sent IPC message to task B
    [experimental task B] activation 39: received IPC message {sender: 1, seq: 39} -- OK
    [experimental task A] activation 40: sent IPC message to task B
    [experimental task B] activation 40: received IPC message {sender: 1, seq: 40} -- OK
    [experimental task A] activation 41: sent IPC message to task B
    [experimental task B] activation 41: received IPC message {sender: 1, seq: 41} -- OK
    [experimental task A] activation 42: sent IPC message to task B
    [experimental task B] activation 42: received IPC message {sender: 1, seq: 42} -- OK
    [experimental task A] activation 43: sent IPC message to task B
    [experimental task B] activation 43: received IPC message {sender: 1, seq: 43} -- OK
    [experimental task A] activation 44: sent IPC message to task B
    [experimental task B] activation 44: received IPC message {sender: 1, seq: 44} -- OK
    [experimental task A] activation 45: sent IPC message to task B
    [experimental task B] activation 45: received IPC message {sender: 1, seq: 45} -- OK
    [experimental task A] activation 46: sent IPC message to task B
    [experimental task B] activation 46: received IPC message {sender: 1, seq: 46} -- OK
    [experimental task A] activation 47: sent IPC message to task B
    [experimental task B] activation 47: received IPC message {sender: 1, seq: 47} -- OK
    [experimental task A] activation 48: sent IPC message to task B
    [experimental task B] activation 48: received IPC message {sender: 1, seq: 48} -- OK
    [experimental task A] activation 49: sent IPC message to task B
    [experimental task B] activation 49: received IPC message {sender: 1, seq: 49} -- OK
    [experimental task A] activation 50: sent IPC message to task B
    [experimental task B] activation 50: received IPC message {sender: 1, seq: 50} -- OK
    [experimental task A] activation 51: sent IPC message to task B
    [experimental task B] activation 51: received IPC message {sender: 1, seq: 51} -- OK
    [experimental task A] activation 52: sent IPC message to task B
    [experimental task B] activation 52: received IPC message {sender: 1, seq: 52} -- OK
    [experimental task A] activation 53: sent IPC message to task B
    [experimental task B] activation 53: received IPC message {sender: 1, seq: 53} -- OK
    [experimental task A] activation 54: sent IPC message to task B
    [experimental task B] activation 54: received IPC message {sender: 1, seq: 54} -- OK
    [experimental task A] activation 55: sent IPC message to task B
    [experimental task B] activation 55: received IPC message {sender: 1, seq: 55} -- OK
    [experimental task A] activation 56: sent IPC message to task B
    [experimental task B] activation 56: received IPC message {sender: 1, seq: 56} -- OK
    [experimental task A] activation 57: sent IPC message to task B
    [experimental task B] activation 57: received IPC message {sender: 1, seq: 57} -- OK
    [experimental task A] activation 58: sent IPC message to task B
    [experimental task B] activation 58: received IPC message {sender: 1, seq: 58} -- OK
    [experimental task A] activation 59: sent IPC message to task B
    [experimental task B] activation 59: received IPC message {sender: 1, seq: 59} -- OK
    [experimental task A] activation 60: sent IPC message to task B
    [experimental task B] activation 60: received IPC message {sender: 1, seq: 60} -- OK
    [experimental task A] activation 61: sent IPC message to task B
    [experimental task B] activation 61: received IPC message {sender: 1, seq: 61} -- OK
    [experimental task A] activation 62: sent IPC message to task B
    [experimental task B] activation 62: received IPC message {sender: 1, seq: 62} -- OK
    [experimental task A] activation 63: sent IPC message to task B
    [experimental task B] activation 63: received IPC message {sender: 1, seq: 63} -- OK
    [experimental task A] activation 64: sent IPC message to task B
    [experimental task B] activation 64: received IPC message {sender: 1, seq: 64} -- OK
    [experimental task A] activation 65: sent IPC message to task B
    [experimental task B] activation 65: received IPC message {sender: 1, seq: 65} -- OK
    [experimental task A] activation 66: sent IPC message to task B
    [experimental task B] activation 66: received IPC message {sender: 1, seq: 66} -- OK
    [experimental task A] activation 67: sent IPC message to task B
    [experimental task B] activation 67: received IPC message {sender: 1, seq: 67} -- OK
    [experimental task A] activation 68: sent IPC message to task B
    [experimental task B] activation 68: received IPC message {sender: 1, seq: 68} -- OK
  experimental isolated-tasks self-test: C 60 ok / 0 failed, D 60 ok / 0 failed (across 60 switches each, repeated CR3 reload)
  experimental scheduler: task 1 kernel stack 0x14000001000..0x14000005000 (4 pages) has guard page 0x14000000000 unmapped -- OK
  experimental scheduler: task 2 kernel stack 0x14000006000..0x1400000a000 (4 pages) has guard page 0x14000005000 unmapped -- OK
  experimental scheduler: task 3 kernel stack 0x1400000b000..0x1400000f000 (4 pages) has guard page 0x1400000a000 unmapped -- OK
  experimental scheduler: task 4 kernel stack 0x1400001c000..0x14000020000 (4 pages) has guard page 0x1400001b000 unmapped -- OK
  experimental scheduler: task 5 kernel stack 0x14000021000..0x14000025000 (4 pages) has guard page 0x14000020000 unmapped -- OK
  experimental scheduler self-test: all 5 task kernel stacks carry a genuinely unmapped guard page directly below them -- OK
  experimental dex-demo: crypto known-answer -- Adler-32("Wikipedia")=0x11e60398 (expected 0x11e60398) OK, SHA-1("abc") 20-byte digest OK
  experimental dex-demo: parsed a real DEX file (file_size=128, map_entry_count=1, all section counts zero as constructed) -- OK
  experimental dex-demo: Adler-32 checksum + SHA-1 signature verified in-kernel -- OK
  experimental dex-demo: corrupted magic -- parse result Some(BadMagic), correctly rejected: true
  experimental dex-demo: corrupted map self-reference -- parse result Some(NoMapListEntry), correctly rejected: true
  experimental dex-demo: corrupted checksum field -- verify result Some(BadChecksum), correctly rejected: true
  experimental dex-demo self-test: crypto known-answer OK, valid parse+integrity OK, bad-magic rejection OK, map-self-reference rejection OK, checksum rejection OK -- OK
  experimental teardown-demo: space X used (wrote+read 0xdeadc0dedeadc0de -- OK), 10 frames reclaimed on destroy (316 -> 306 in-use, baseline 306) -- fully reclaimed
  experimental teardown-demo: space Y's PML4 (0x36c000) == space X's just-freed PML4 (0x36c000) -- OK, frame genuinely reused
  experimental ring3-scheduler: 3 tasks built (E entry 0x50000000000; F, G pre-loaded with initial trap blobs), target 21 total yields (7 per task)
  experimental ring3-scheduler: task E kernel RSP0 stack 0x1400002c000..0x14000031000 (5 pages) has guard page 0x1400002b000 unmapped -- OK
  experimental ring3-scheduler: task F kernel RSP0 stack 0x14000032000..0x14000037000 (5 pages) has guard page 0x14000031000 unmapped -- OK
  experimental ring3-scheduler: task G kernel RSP0 stack 0x14000038000..0x1400003d000 (5 pages) has guard page 0x14000037000 unmapped -- OK
  experimental ring3-scheduler self-test: all 3 live task kernel stacks carry a genuinely unmapped guard page directly below them -- OK
  experimental ring3-scheduler self-test: task E counter=7 (expected 7) -- OK
  experimental ring3-scheduler self-test: task F counter=7 (expected 7) -- OK
  experimental ring3-scheduler self-test: task G counter=7 (expected 7) -- OK
  experimental ring3-scheduler self-test: 21 real trap-based yields processed, all 3 tasks' full register + address-space state correctly suspended and resumed -- OK
  experimental ring3-scheduler teardown: task F's address space (pml4 0x381000, active for its own 7 trap-based yields) destroyed after suspension -- 15 frames reclaimed (357 -> 342 in use) -- OK
  experimental ring3-scheduler teardown: LIVE slot hand-back -- destroyed task F's RSP0 slot (base 0x14000032000, guard 0x14000031000) re-handed out byte-identically as task H's live RSP0 stack, re-verified through H's page tables; task I bumped a fresh slot instead -- OK, the teardown path feeds the exact-count pool
  experimental ring3-scheduler: task H kernel RSP0 stack 0x14000032000..0x14000037000 (5 pages) has guard page 0x14000031000 unmapped -- OK
  experimental ring3-scheduler: task I kernel RSP0 stack 0x1400003e000..0x14000043000 (5 pages) has guard page 0x1400003d000 unmapped -- OK
  experimental ring3-scheduler self-test: all 2 preemption-proof task kernel stacks carry a genuinely unmapped guard page directly below them -- OK
  experimental ring3-scheduler: timer preemption armed -- entering task H, whose program never traps; IRQ0 is the only reschedule point from here (target 6 switches)
  experimental ring3-scheduler preemption self-test: task H counter=24146012 (>0 required -- its program contains no trap instruction, so only a timer switch could ever let it run) -- OK
  experimental ring3-scheduler preemption self-test: task I counter=30824029 (>0 required -- its program contains no trap instruction, so only a timer switch could ever let it run) -- OK
  experimental ring3-scheduler preemption self-test: task H force-switched off the CPU 3 times, task I 3 times (6 timer-driven switches, target 6; 0 cooperative yields during the phase) -- OK
  experimental ring3-scheduler preemption self-test: two non-yielding ring-3 tasks round-robined purely by IRQ0 -- timer-driven preemption, not cooperative-only -- OK
  experimental pie-demo: GNU_RELRO region 0x60000002ef0..0x60000003000 is back to read-only after relocation (loader grant/revoke cycle completed) -- OK
  experimental pie-demo: ET_DYN loaded at base 0x60000000000, entry 0x60000001000 (bias correctly applied by the loader, not baked into the file)
  experimental pie-demo self-test: relocated pointer dereferenced to 0xfeedface12345678 (expected 0xfeedface12345678) -- OK, relocation correctly applied
  experimental pie-demo relro check: page 0x60000002ef0 PTE flags writable=false -- OK
  experimental pie-demo self-test: all 1 GNU_RELRO page(s) verified read-only in the live page tables after relocation -- OK
  experimental elf-dyn-demo: GNU_RELRO region 0x64000002ef0..0x64000003000 revoked read-only after grant/relocate (the GRANT half ran for the first time) -- OK
  experimental elf-dyn-demo: resolved by name via the DT_HASH walk -- dyn_payload 0x64000001000, dyn_value 0x64000003000, dyn_timestamp 0x64000003010 -- OK
  experimental elf-dyn-demo: absent-name lookup returned the typed SymbolNotFound (not a parse error) -- OK
  experimental elf-dyn-demo: GLOB_DAT slot 0x64000002fe0 (in-RELRO .got) == 0x64000003000 (resolved dyn_value) -- OK, symbol-resolved relocation applied correctly
  experimental elf-dyn-demo: R_X86_64_64 slot 0x64000003008 (writable .data) == 0x64000003000 (resolved dyn_value) -- OK, symbol-resolved relocation applied correctly
  experimental elf-dyn-demo: RELRO page 0x64000002000 is read-only and user-readable after the grant/revoke cycle -- OK
  experimental elf-dyn-demo: GLOB_DAT slot residency check: 0x64000002fe0 inside RELRO region 0x64000002ef0..0x64000003000 = true (true expected -- ld placed .got in the GNU_RELRO region; reads are legal, writes protected) -- OK
  experimental elf-dyn-demo: wrote 0x5eed0001 through resolved dyn_timestamp (0x64000003010) and read it back -- OK, a resolved runtime address is a real mapped address
  experimental elf-dyn-demo: kernel-side proof complete; entering dyn_payload (ring 3) which dereferences its own resolved GOT slot
  experimental elf-dyn-demo self-test: the .so's own code read 0xd1ce00f00d through its resolved GOT slot (expected 0xd1ce00f00d) -- OK
  experimental cross-demo: provider's GNU_RELRO region 0x6c000001f40..0x6c000002000 revoked read-only (revoke-only; its layout has no in-region relocations) -- OK
  experimental cross-demo: provider registered (registry 1 object(s)); cross_value 0x6c000002000, dup_symbol 0x6c000002008 resolve from the provider's own tables -- OK
  experimental cross-demo: dependent's GNU_RELRO region 0x6c000042ee8..0x6c000043000 revoked read-only; the grant half ran for both in-region GLOB_DAT slots (one UNDEF-but-resolved) -- OK
  experimental cross-demo: the app's own by-name lookup of cross_value refused with UndefinedSymbol -- OK, the app genuinely does not define it
  experimental cross-demo: app's UNDEF GLOB_DAT slot 0x6c000042fe0 == 0x6c000002000 (the PROVIDER's cross_value, via the registry) -- OK, cross-object relocation applied correctly
  experimental cross-demo: app's duplicate-name GLOB_DAT slot 0x6c000042fd8 == 0x6c000043000 (ITS OWN dup_symbol) -- OK, self-priority: the app's OWN dynsym entry won, not the registry's earlier same-named entry
  experimental cross-demo: registry search order decides -- [app,lib] -> 0x6c000043000 (app), [lib] -> 0x6c000002008 (lib) -- OK, first DEFINED match in order wins
  experimental cross-demo: absent-name registry lookup returned Ok(None) (typed absent, no parse error) -- OK
  experimental cross-demo: the same dependent WITHOUT a resolver refused with Ok(relro = Denied) -- the caller must refuse -- OK, the refusal is NOT gone now that resolution exists
  experimental cross-demo: kernel-side proof complete; entering cross_payload (ring 3), which dereferences its registry-resolved GOT slot
  experimental cross-demo self-test: the dependent's own code read 0xace5f00dd1ce5eed through its cross-resolved GOT slot (expected 0xace5f00dd1ce5eed, the provider's cross_value) -- OK
  experimental full-demo: provider loaded; DT_SONAME fact (crosslib.so.1) validated -- OK
  experimental full-demo: same-SONAME V2 provider refuses the VERS_1.0 requirement with VersionNotProvided -- OK, version matching is enforced; the name alone is NOT enough
  experimental full-demo: dependent's GNU_RELRO region 0x6c000202e68..0x6c000203000 revoked read-only after the version-checked grant/relocate -- OK
  experimental full-demo: dependent's versioned GLOB_DAT slot 0x6c000202fe0 == 0x6c000003000 (provider's cross_value, version-checked) -- OK
  experimental full-demo: dependent's duplicate-name slot 0x6c000202fd8 == 0x6c000203008 (its OWN dup_symbol) -- OK, self-priority held under the versioned provider too
  experimental full-demo: lazy JUMP_SLOT slot 0x6c000203000 == 0x6c000210000 (trap stub armed; NOT eagerly resolved) -- OK
  experimental full-demo: empty-registry dependent refused with MissingDependency -- OK, the declared dependency contract is enforced
  experimental full-demo: V2-registry dependent refused (denied region or typed version error) -- OK, the version contract refuses end to end
  experimental full-demo: lazy-bind service probe of 0x6c000210000 while the registry was EMPTY refused with the typed ServiceEmpty -- OK, the empty branch proven kernel-side before the chain's first registration
  experimental full-demo self-test: cross_value via versioned slot = 0xace5f00dd1ce5eed (expected 0xace5f00dd1ce5eed) -- OK
  experimental full-demo self-test: cross_fn results (eax) 0xfa571ae5 / 0xfa571ae5 (expected 0xfa571ae5 both; first via lazy bind, later direct) -- OK
  experimental full-demo self-test: lazy-bind trap fired 1 time(s) across 3 calls (exactly 1 required) -- OK, binding is lazy; later calls are direct
  experimental full-demo: shared lazy-bind service: 1 object(s) registered so far, 1 bind(s) served through ONE vector (0x88) this boot -- OK, this dependent binds through the service, not a demo static
  experimental deps-demo: GNU walk of a name in NEITHER of A's tables refused with SymbolNotFound -- OK (absent, not malformed: GNU-absent is typed as absent)
  experimental deps-demo: provider A carries BOTH hash tables (DT_HASH authority + DT_GNU_HASH); GNU walk == SysV walk on cross_value (0x6d000003000) -- OK, the GNU mechanism's answer matches the SysV mechanism's
  experimental deps-demo: provider A loaded (crosslib.so.1) -- OK
  experimental deps-demo: pre-overlay lookup of interp_mark = absent (provider A does not define it) -- OK, absent before the overlay exists
  experimental deps-demo: after interpose(overlay), lookup of interp_mark = 0x6d000103000 (data 0x1111222233334444, the overlay's value) -- OK, interposition overrides load order
  experimental deps-demo: looping catalog refused with DependencyCycle -- OK, a file can never load itself to satisfy itself
  experimental deps-demo: DT_NEEDED CHAIN (dependent -> crosslib2.so.1 -> crosslib3.so.1 -> crosslib4.so.1 -> crosslib.so.1) satisfied dependency-first; 3 catalog object(s) auto-loaded -- OK, the DEPTH-3 chain (B, C and D) loaded from the catalog; A was already in scope
  experimental deps-demo: depth-3 chain under a catalog whose cap is 2 refused with DependencyDepthExceeded (at the D hop, B and C mapped, A never reached) -- OK
  experimental deps-demo: 3 auto-loaded chain providers carry their DISTINCT marker immediates at their own bases (3/3 symbols found, contents == the source constants) -- OK, the right file loaded at the right base, per object
  experimental deps-demo: dependent's GNU_RELRO revokes read-only WITH an unresolved WEAK entry in-region (weak != denial) -- OK
  experimental deps-demo: dependent's versioned GLOB_DAT slot 0x6d000302fe0 == 0x6d000003000 (provider's cross_value as resolved BY THE CATALOG CHAIN) -- OK
  experimental deps-demo: dependent's interp_mark slot 0x6d000302fd8 == 0x6d000103000 (the INTERPOSITION overlay's address, not that of anything provider A defines) -- OK
  experimental deps-demo: WEAK UNDEF ghost_fn@VERS_1.0 slot 0x6d000302fd0 == 0x0 (the NULL slot; unresolved weak stays non-fatal) -- OK
  experimental deps-demo: lazy JUMP_SLOT slot 0x6d000303000 == 0x6d000310000 (trap stub armed on THIS object's own trampoline page; NOT eagerly resolved) -- OK
  experimental deps-demo: WEAK version-flag artifact resolves cross_value@VERS_1.0 against the providing scope (weak relaxes only the UNRESOLVED case) -- OK
  experimental deps-demo: GNU walk of the weak artifact's UNDEF cross_value reports ABSENT (SymbolNotFound) while the SysV walk reports UndefinedSymbol -- OK (GNU excludes UNDEF entries from its buckets; DT_HASH chains them; the asymmetry is typed)
  experimental deps-demo: WEAK version-flag requirement with unmet VERS_1.0 takes the NULL slot (0), load proceeds, relro read-only -- OK (a STRONG requirement is the typed VersionNotProvided refusal in the same position)
  experimental deps-demo: lazy-bind service refuses an UNREGISTERED trampoline page (0x6d000570000) with the typed UnregisteredTrampoline carrying that page; registry size 1 -> 1 and binds 1 -> 1 unchanged -- OK (the handler's panic path is demonstrated, not assumed)
  experimental deps-demo self-test: cross_value = 0xace5f00dd1ce5eed (expected 0xace5f00dd1ce5eed) -- OK
  experimental deps-demo self-test: interp_mark = 0x1111222233334444 (expected 0x1111222233334444, the overlay's value read through ring 3) -- OK
  experimental deps-demo: lazy-bind service fired 1 time(s) across 3 calls (exactly 1 required) -- OK, binding is lazy; calls 2-3 are direct
  experimental deps-demo: shared lazy-bind service: 2 independent object(s) registered, 2 bind(s) served through ONE vector (0x88) this boot -- OK, the vector is a SERVICE, not a demo
  experimental plt-demo: provider3 loaded at 0x6d000770000: DT_SONAME pltprov.so.1 AND its prov_marker data reads back 0xe11cede1e1e1e1e -- OK, the RIGHT file at the RIGHT base
  experimental plt-demo: registry probe of link-map id 0xbeefc0de (no GNU object registered yet) refused with the typed UnregisteredLinkMap carrying THAT id -- OK, an unregistered cookie is refused, never guessed
  experimental plt-demo: instance 1 own-.plt armed on THE OBJECT's own .got.plt (0x6d000702fe8): GOT[1] = link-map id 0x1, GOT[2] = resolver thunk 0x6d000710000 (1 .rela.plt entries) -- OK, the GNU resolver pointers are in place (JUMP_SLOT slots touched only by the GNU lazy bias relocation)
  experimental plt-demo: instance 1 JUMP_SLOT idx 0 slot 0x6d000703000 == 0x6d000701016 pre-run (the LINKER's own plt+6 initial value with the GNU lazy relocation bias applied; not resolved; expected live value 0x6d000661000) -- OK
  experimental plt-demo: instance 1 registered under link-map id 0x1; the shared service now holds 3 object(s) -- OK, the GNU path lives in the SAME registry the trap-stub path uses
  experimental plt-demo self-test: instance 1 -- cross_value = 0xace5f00dd1ce5eed (expected 0xace5f00dd1ce5eed) -- OK
  experimental plt-demo self-test: instance 1 -- cross_fn results (eax) 0xfa571ae5 / 0xfa571ae5 / 0xfa571ae5 (expected 0xfa571ae5 all; call 1 entered the callee DIRECTLY via the resolver's guided return) -- OK
  experimental plt-demo self-test: instance 1 -- its REAL .got.plt slot 0x6d000703000 patched to the resolved cross_fn (0x6d000661000) -- OK
  experimental plt-demo self-test: instance 1 -- first-use binds for ITS OWN cookie 0x1: 1 across 3 calls (exactly 1 required) -- OK, GNU lazy semantics on THIS object's own .plt machinery
  experimental plt-demo: instance 2 own-.plt armed on THE OBJECT's own .got.plt (0x6d000742fe8): GOT[1] = link-map id 0x2, GOT[2] = resolver thunk 0x6d000750000 (1 .rela.plt entries) -- OK, the GNU resolver pointers are in place (JUMP_SLOT slots touched only by the GNU lazy bias relocation)
  experimental plt-demo: instance 2 JUMP_SLOT idx 0 slot 0x6d000743000 == 0x6d000741016 pre-run (the LINKER's own plt+6 initial value with the GNU lazy relocation bias applied; not resolved; expected live value 0x6d000661000) -- OK
  experimental plt-demo: instance 2 registered under link-map id 0x2; the shared service now holds 4 object(s) -- OK, the GNU path lives in the SAME registry the trap-stub path uses
  experimental plt-demo self-test: instance 2 -- cross_value = 0xace5f00dd1ce5eed (expected 0xace5f00dd1ce5eed) -- OK
  experimental plt-demo self-test: instance 2 -- cross_fn results (eax) 0xfa571ae5 / 0xfa571ae5 / 0xfa571ae5 (expected 0xfa571ae5 all; call 1 entered the callee DIRECTLY via the resolver's guided return) -- OK
  experimental plt-demo self-test: instance 2 -- its REAL .got.plt slot 0x6d000743000 patched to the resolved cross_fn (0x6d000661000) -- OK
  experimental plt-demo self-test: instance 2 -- first-use binds for ITS OWN cookie 0x2: 1 across 3 calls (exactly 1 required) -- OK, GNU lazy semantics on THIS object's own .plt machinery
  experimental plt-demo: instance 3 own-.plt armed on THE OBJECT's own .got.plt (0x6d000782fe8): GOT[1] = link-map id 0x3, GOT[2] = resolver thunk 0x6d000790000 (2 .rela.plt entries) -- OK, the GNU resolver pointers are in place (JUMP_SLOT slots touched only by the GNU lazy bias relocation)
  experimental plt-demo: instance 3 JUMP_SLOT idx 0 slot 0x6d000783000 == 0x6d000781016 pre-run (the LINKER's own plt+6 initial value with the GNU lazy relocation bias applied; not resolved; expected live value 0x6d000771010) -- OK
  experimental plt-demo: instance 3 JUMP_SLOT idx 1 slot 0x6d000783008 == 0x6d000781026 pre-run (the LINKER's own plt+6 initial value with the GNU lazy relocation bias applied; not resolved; expected live value 0x6d000771000) -- OK
  experimental plt-demo: instance 3 registered under link-map id 0x3; the shared service now holds 5 object(s) -- OK, the GNU path lives in the SAME registry the trap-stub path uses
  experimental plt-demo self-test: instance 3 (multi-entry) -- fn_a = 0xa11ce0de (expected 0xa11ce0de), fn_b = 0xbb0b5e11 (expected 0xbb0b5e11, TWO calls, second direct) -- OK
  experimental plt-demo self-test: instance 3 -- .rela.plt idx 0 slot 0x6d000783000 patched to 0x6d000771010 (expected 0x6d000771010, ITS OWN index) -- OK
  experimental plt-demo self-test: instance 3 -- .rela.plt idx 1 slot 0x6d000783008 patched to 0x6d000771000 (expected 0x6d000771000, ITS OWN index) -- OK
  experimental plt-demo self-test: MULTI-ENTRY artifact cookie 0x3: 2 first-use binds (EXACTLY 2 -- one per .rela.plt entry, one cookie); shared service now 5 object(s) / 6 bind(s) across both gates (0x88 trap stubs + 0x8B GNU own-.plt) -- OK, one registry serves a MULTI-ENTRY GNU object AND the single-entry AND trap-stub paths
  experimental plt-demo: GNU OWN-.plt mode requested on the PLT-LESS artifact REFUSED with the typed MalformedDynamicTables (no .got.plt machinery to arm) -- OK, the protocol refuses cleanly where its machinery does not exist
  experimental plt-demo: the SAME PLT-LESS artifact loads eagerly: its GLOB_DAT slot 0x6d0007b2fe0 == provider A's cross_value (0x6d000663000) resolved AT LOAD, relro revoked read-only, NO GNU arming side effects (registry 5 -> 5, binds 6 -> 6) -- OK, the GNU protocol is opt-in per load; eager stays the default for PLT-less objects
  experimental pe-demo: PE32+ loaded at its own ImageBase (no relocation), entry 0x140001000
  experimental pe-demo self-test: PE entry point wrote 0xfeedc0defeedc0de (expected 0xfeedc0defeedc0de) -- OK, a genuine Windows-format (PE32+) executable's own code ran in ring 3
  experimental pe-reloc-demo: PE32+ loaded at 0x90000000000 (its ImageBase is 0x140000000); .data pointer relocated to 0x90000002008 (expected 0x90000002008)
  experimental pe-reloc-demo self-test: relocated pointer wrote 0xfeedfacecafebeef (expected 0xfeedfacecafebeef) -- OK, a relocatable PE32+ binary ran correctly at a loader-chosen address
  experimental pe-import-demo: provider loaded at its own ImageBase 0x180000000000; its export table declares "userprog_pe_import_dll.dll"
  experimental pe-import-demo: export table facts -- 2 name(s), lookup(crossfn) = provider+0x1000, lookup(crossmark) = provider+0x2000
  experimental pe-import-demo: provider export lookup self-test: crossfn 0x180000001000, crossmark 0x180000002000, absent symbol None -- OK
  experimental pe-import-demo: forwarder host loaded at its own ImageBase 0x1c0000000000; its export table declares "userprog_pe_import_fwdprov.dll" with 3 name(s), every EAT entry a directory-internal forwarder -- OK
  experimental pe-import-demo: negative control 1 -- EMPTY registry refused the import walk with the typed MissingDllImport -- OK
  experimental pe-import-demo: negative control 2 -- the DLL name bound to the WRONG FILE's empty export table refused with the typed MissingImport -- OK, the registry identity is the file, not the name
  experimental pe-import-demo: negative control 3 -- a SELF-CYCLING forwarded export refused at the depth cap with the typed ForwarderDepthExceeded -- OK
  experimental pe-import-demo: negative control 4 -- a forwarded export naming an UNREGISTERED DLL refused with the typed MissingDllImport -- OK
  experimental pe-import-demo: ordinal out-of-range / empty-table probes answer typed-None, never a guess -- OK
  experimental pe-import-demo: negative control 5 -- a fresh-stamped descriptor with an ALL-FRESH forwarder ref whose IAT slot contradicts the chased answer refused with the typed BoundValueCorrupt (0xdeadbeef00001000 != 0x180000001000) -- OK, stamps are verified THROUGH the forwarder chains too, never trusted blindly
  experimental pe-import-demo: negative control 6 -- the REFSTALE carrier (one ref stamp byte beyond the bad client's) DISCARDED the same wrong slot (0xdeadbeef00001000) and resolved it fresh through the same chase (0x180000001000); the descriptor's own stamp stayed fresh (0x386d4380) and the ref declared 0x386d4381 -- OK, one ref stamp byte decides refuse-vs-discard
  experimental pe-import-demo: negative control 7 -- the REF2BAD carrier (TWO refs, BOTH FRESH) refused through the two-ref chain with the typed BoundValueCorrupt (0xdeadbeef00001000 != 0x180000001000) -- OK, the count made the walk unambiguous and the fresh surface still verified the slot THROUGH both refs
  experimental pe-import-demo: negative control 8 -- the REF2STALE carrier (same two refs, only the SECOND's stamp moved to 0x386d4381) DISCARDED the same wrong slot (0xdeadbeef00001000) and resolved fresh through the same chase (0x180000001000); the first ref stayed fresh (0x386d4380) -- OK, a LATER ref stamp byte decides refuse-vs-discard across a TWO-ref chain
  experimental pe-import-demo: negative control 9 (positive half) -- the DELAYGOOD carrier's two delay imports eagerly resolved through the same registry (named crossfn -> 0x180000001000, ordinal crossmark -> 0x180000002000; prior contents were the composed unbound thunks, both slots WRITTEN and read back) and the module handle 0x180000000000 was written where the descriptor pointed -- OK, the eager delay ruling matches the normal walk's answers
  experimental pe-import-demo: negative control 10 -- the DELAYBAD carrier's delay descriptor naming 'nowhere.dll' refused with the typed MissingDelayDllImport -- OK, the name-vs-file ruling applies to delay descriptors with their OWN flavor
  experimental pe-import-demo: negative control 11 -- the DELAYMUT carrier (dwTimeStamp = provider stamp + 1, the OLD-BIND marker) refused with the typed MalformedDelayImport -- OK, a binding guarantee the kernel cannot verify is NEVER silently honored and never silently ignored
  experimental pe-import-demo: negative control 12 -- the DELAYATTRS carrier (grAttrs = 0x3, dlattrRva plus an UNDEFINED bit) refused with the typed MalformedDelayImport -- OK, an attribute set the Windows helper itself cannot interpret is refused before any table is trusted
  experimental pe-import-demo: negative control 13 -- the DELAYMIS carrier (INT[0] names crossfn while IAT[0] carries an ordinal thunk) refused with the typed MalformedDelayImport -- OK, the unbound pre-state the walk resolves is checked entry-for-entry, never assumed
  experimental pe-import-demo: negative control 14 -- the DELAYVA carrier (grAttrs = 0, the pre-VC7 VA form, with RVA-sized fields) refused with the typed MalformedDelayImport -- OK, a VA-form field that cannot be a VA of this image is refused, not reinterpreted
  experimental pe-import-demo: negative control 15 -- the DELAYVABIAS carrier (real VA-form descriptor of ImageBase 0x400000, loaded at 0x460000000000) refused with the typed MalformedDelayImport -- OK, a pre-VC7 descriptor's baked VAs are refused in a relocated image instead of being reinterpreted against the wrong base
  experimental pe-import-demo: negative control 16 -- the DELAYMANY carrier's 17th descriptor refused with the typed MalformedDelayImport; descriptors 0-15 are FULLY resolved (their IAT slots hold the provider's crossfn 0x180000001000, their handle slots the provider's mapped base), while the 17th's slot still holds its composed unbound thunk 0x276b and its handle slot is untouched -- OK, the 16-descriptor cap is EXCLUSIVE: the legal maximum is resolved in full and only the entry past it is refused
  experimental pe-import-demo: negative control 17 -- the DELAYTHUNKS carrier's 65th thunk entry refused with the typed MalformedDelayImport; entries 0-63 are FULLY resolved (all 64 IAT slots hold the provider's crossfn 0x180000001000) while entry 64 still holds its composed unbound thunk 0x271b -- OK, the 64-entry thunk cap is EXCLUSIVE for the same reason, and one descriptor's INT/IAT pair was walked end to end before the refusal
  experimental pe-import-demo: negative control 18 -- the DELAYFULL carrier (EXACTLY 16 descriptors, the FIRST carrying EXACTLY 64 thunk entries, 79 imports) ACCEPTED and resolved completely: every slot holds the provider's crossfn 0x180000001000, all 16 handle slots the provider's base, and the IAT terminator right after the 64th entry stopped the walk cleanly -- OK, both caps are exclusive bounds: the legal maxima are accepted, one past them is refused
  experimental pe-import-demo: BOUND-IMPORT facts -- provider's fresh-stamp entry: 2 slot(s) HONORED (prefills verified == fresh resolution, slots left un-written); the stale entry (stamp = provider's + 1, a stamp no file carries): its pre-bound contradiction 0x180000002000 DISCARDED and resolved fresh to 0x180000001000; nobody.dll's inert entry parsed and ignored -- OK
  experimental pe-import-demo: resolved 3 import(s) through the REAL import directories -- OK: named crossfn -> 0x180000001000 and ordinal-form crossmark (`ord2`) -> 0x180000002000 both HONORED by stamp (verified prefills, left un-written); forwarder crossfn_fwd -> 0x180000001000 (the PROVIDER's .text, chased through the forwarder host's export entry after its stale pre-binding was DISCARDED); every slot reads back its (verified or written) value
  experimental pe-import-demo: IAT slots -- crossfn 0x180000001000, ordinal crossmark 0x180000002000, forwarder crossfn_fwd 0x180000001000 (slot 21990232563816; slot 21990232563824; slot 21990232563840) -- OK
  experimental pe-import-demo: resolved crossmark address dereferences to 0xace5f00dd1ce5eed (expected 0xace5f00dd1ce5eed, the provider's own .data immediate) -- OK
  experimental pe-import-demo: kernel-side proof complete; entering the imported client (ring 3) which dereferences crossmark through its resolved IAT slot and calls crossfn through its own
  experimental pe-import-demo self-test: imported data read 0xace5f00dd1ce5eed through the resolved IAT slot (expected 0xace5f00dd1ce5eed); imported function returned 0xfa571ae5 (expected 0xfa571ae5); forwarder-resolved import returned 0xfa571ae5 (expected 0xfa571ae5, the PROVIDER's own code executed through the forwarder host's export entry) -- OK, a real Windows-format executable's own imports -- named, ordinal, and forwarder-chased -- all resolved through real Windows-format export tables (GetProcAddress semantics), and its own code used them in ring 3
  experimental isolation: address space A built (pml4 0x4fd000), program A loaded, entry 0x20000000000
  experimental isolation: trap from process A (cpl=3), read 0xc0ffee (expected 0xc0ffee) -- OK
  experimental isolation: address space B built (pml4 0x508000), program B loaded, entry 0x20000000000 -- switched active, A is now inactive but preserved
  experimental isolation: trap from process B (cpl=3), read 0xbbbbbbbb (expected 0xbbbbbbbb) -- OK
  experimental isolation: switched back to process A's address space, re-read 0xc0ffee (expected 0xc0ffee, unchanged since A ran) -- OK, ISOLATED
  experimental isolation self-test: two independent address spaces verified mutually isolated -- OK
  memory stack-reuse self-test: released 2-page slot re-handed out byte-identically (base 0x14000092000, guard 0x14000091000, top 0x14000094000) fully re-verified with its guard unmapped; an 8-page release absorbed WHOLE by 3-page and by 2-page requests (same guard/top, descriptor stays 8 pages, zero splits); a bigger request never touched a smaller pooled slot -- OK, exact-match preferred, whole-slot absorption bounded, no rewind
  memory guard-demo: stack mapped at 0x1400009e000..0x140000a2000 (4 pages); bottom present OK, top present OK, guard page 0x1400009d000 absent OK, page above 0x140000a2000 absent OK
  memory guard-demo self-test: guarded stack correctly built (4 usable pages, page below genuinely unmapped, page above genuinely unmapped) -- OK
  memory guard-demo: arming terminal overflow probe -- writing one qword at the guard page 0x1400009d000 (expect a PROTECTION_VIOLATION page fault)
  memory guard-demo self-test: overflow write to guard page 0x1400009d000 faulted exactly as required (accessed 0x1400009d000, error code PageFaultErrorCode(CAUSED_BY_WRITE)) -- OK, a stack overflow is caught deterministically instead of corrupting memory below it
nyrqis kernel: phase 1+2+3+4+ring3-elf-isolation-poc+guard-pages complete
```

- And the UEFI gate's guest output (its first 14 non-kernel lines are
  the OVMF/stub handoff section catalogued above):

```
BdsDxe: loading Boot0001 "UEFI QEMU HARDDISK QM00001 " from PciRoot(0x0)/Pci(0x1F,0x2)/Sata(0x0,0xFFFF,0x0)
BdsDxe: starting Boot0001 "UEFI QEMU HARDDISK QM00001 " from PciRoot(0x0)/Pci(0x1F,0x2)/Sata(0x0,0xFFFF,0x0)

nyrqis ubld: stub alive under UEFI; SecureBoot=absent
nyrqis ubld: kernel image loaded from the stub's own bytes; uefi_entry resolved
nyrqis ubld: memory map acquired
nyrqis ubld: rsdp found (acpi config table, rev=2.0)
nyrqis ubld: Multiboot2 info synthesized (map entries + rsdp + vendor tag)
acpi-reclaim region: base=0xf76d000 pages=0x12
0x6:0xc70 0x7d1:0x12 0xf:0x2c 0x0:0x8 

[s] entering EBS
[s] EBS ok
[s] cr3 switched; jumping uefi_entry
nyrqis kernel: phase 1 bring-up
  gdt: loaded (kernel code segment + tss)
  idt: loaded (divide-error, invalid-opcode, gpf, page-fault, double-fault)
  double-fault ist stack: index 0
  multiboot: memory map parsed, 62247 usable 4KiB frames (243 MiB)
  acpi: RSDP v2 validated (XSDT root table), 6 tables enumerated and checksum-verified
  acpi reclaim: representative frame 0xf76d000 usable before=false (expected false), 1 reclaimable region(s) released, usable after=true (expected true) -- OK
  paging: CR0.WP set by the kernel (supervisor writes honor PTE RO on both boot paths -- documented boot contract, not firmware state)
  paging: dynamic mapper live (CR3 read back, OffsetPageTable constructed), NX enabled (EFER.NXE)
  heap: mapped 256 pages (1024 KiB) at 0x10000000000, global allocator live
  heap self-test: Vec<u32> of 100 elements allocated and summed correctly (5050)
  frames allocated so far: 259
  memory tlb-demo: mapped (frame Some(PhysAddr(0x33d000))), unmapped (translate -> None), remapped (frame Some(PhysAddr(0x341000))); re-read 0xbbbbbbbbbbbbbbbb (expected 0xbbbbbbbbbbbbbbbb)
  memory tlb-demo self-test: present->absent->present OK, backing frame genuinely changed OK, remap read back new bytes OK -- OK
  pic: remapped (IRQ0-7 -> 0x20-0x27, IRQ8-15 -> 0x28-0x2F), timer unmasked
  rtc: periodic interrupt armed at 1024 Hz, IRQ8 + cascade IRQ2 unmasked
  keyboard: 8042 keyboard interrupt (IRQ1, master PIC) enabled and unmasked
  interrupts: enabled (sti)
  timer self-test: 20 real hardware timer interrupts observed and handled
  rtc self-test: 4 additional RTC periodic interrupts observed and handled after a snapshot (1063 total; IRQ8, via the slave-PIC cascade)
  rtc wall-clock self-test: first read Ok(RtcTime { second: 14, minute: 8, hour: 7 }) (decoded via the hardware's actual mode bits, fields in valid ranges) -- OK
  rtc wall-clock self-test: bounded-UIP second read Ok(RtcTime { second: 14, minute: 8, hour: 7 }) completed without hanging -- OK
  rtc wall-clock self-test: second read does not move the clock backwards beyond a tolerated skew -- OK
  rtc wall-clock self-test: 6 periodic tick(s) observed between the two wall-clock reads (the periodic line and the wall-clock registers are both alive) -- OK
  rtc wall-clock self-test: wall-clock reads decode via the hardware's reported modes and order monotonically -- OK
  keyboard self-test: injected scancode 0x1e via the 8042 output-buffer command (0xD2), 1 interrupt(s) handled, handler read back 0x1e -- OK
  ata: no IDE device on either channel -- IRQ14/IRQ15 handlers installed but left masked (nothing to prove)
nyrqis kernel: phase 1+2+3-prep complete, boot phase = 7 (Ready)
  experimental scheduler: 4 tasks registered (lifecycle-enforcing, NPS-002 §5)
  experimental ipc: 4 mailboxes initialized (arbiter-checked, NPS-003 §5.4)
    [experimental task A] activation 1: sent IPC message to task B
    [experimental task B] activation 1: received IPC message {sender: 1, seq: 1} -- OK
    [experimental task J] activation 1: terminating per NPS-002 §5.5/§5.6 (RUNNING -> TERMINATING -> TERMINATED, then dispatch away)
    [experimental task A] activation 2: sent IPC message to task B
    [experimental task B] activation 2: received IPC message {sender: 1, seq: 2} -- OK
    [experimental task A] activation 3: sent IPC message to task B
    [experimental task B] activation 3: received IPC message {sender: 1, seq: 3} -- OK
    [experimental task A] activation 4: sent IPC message to task B
    [experimental task B] activation 4: received IPC message {sender: 1, seq: 4} -- OK
    [experimental task A] activation 5: sent IPC message to task B
    [experimental task B] activation 5: received IPC message {sender: 1, seq: 5} -- OK
    [experimental task A] activation 6: sent IPC message to task B
    [experimental task B] activation 6: received IPC message {sender: 1, seq: 6} -- OK
  experimental scheduler termination proof: task J walked RUNNING -> TERMINATING -> TERMINATED and was never re-dispatched (ran exactly 1 time) -- OK
  experimental scheduler self-test: 6 round trips completed, task A ran 6 times, task B ran 6 times
  experimental ipc self-test: 6 messages sent task A -> task B and received correctly, 0 mismatches
  experimental scheduler lifecycle self-test: all 6 transitions attempted from Created matched the edge table (accepted iff legal) -- OK
  experimental scheduler lifecycle self-test: all 6 transitions attempted from Ready matched the edge table (accepted iff legal) -- OK
  experimental scheduler lifecycle self-test: all 6 transitions attempted from Running matched the edge table (accepted iff legal) -- OK
  experimental scheduler lifecycle self-test: all 6 transitions attempted from Blocked matched the edge table (accepted iff legal) -- OK
  experimental scheduler lifecycle self-test: all 6 transitions attempted from Terminating matched the edge table (accepted iff legal) -- OK
  experimental scheduler lifecycle self-test: all 6 transitions attempted from Terminated matched the edge table (accepted iff legal) -- OK
  experimental scheduler lifecycle self-test: 36/36 from-to pairs verified (9 legal accepts, 27 illegal refusals, counter ticked exactly once per refusal) -- OK
  experimental scheduler termination self-test: 1 terminated task(s) (reached via the legal path), TERMINATED unreachable directly from CREATED/READY, remaining tasks active -- OK
  experimental ipc arbiter self-test: honest send from the running task -- OK (queued)
  experimental ipc arbiter self-test: forged sender (claims 1, running 0) refused with NotRunningTask -- OK
  experimental ipc revocation self-test: revoke_sender(scratch, 0) newly-revoked -- OK
  experimental ipc revocation self-test: send after revocation refused with Revoked -- OK
  experimental ipc revocation self-test: message queued before revocation still receivable after it -- OK
  experimental ipc revocation self-test: re-revoking an already-revoked sender is a no-op returning false -- OK
    [experimental task A] activation 7: sent IPC message to task B
    [experimental task B] activation 7: received IPC message {sender: 1, seq: 7} -- OK
    [experimental task A] activation 8: sent IPC message to task B
    [experimental task B] activation 8: received IPC message {sender: 1, seq: 8} -- OK
  experimental ipc blocking-receive self-test: bounded receive on an empty mailbox returned ReceiveTimedOut (typed, never an indefinite block) -- OK
  experimental ipc blocking-receive self-test: bounded receive delivered the message sent before it (Ok(IpcMessage { sender: 0, seq: 7 })) -- OK
  experimental ipc self-test: arbiter + revocation + blocking-receive contract checks -- OK
  experimental isolated-tasks: 6 total tasks in round-robin (main, A, B, C[own cr3 0x350000], D[own cr3 0x35a000])
    [experimental task A] activation 9: sent IPC message to task B
    [experimental task B] activation 9: received IPC message {sender: 1, seq: 9} -- OK
    [experimental task A] activation 10: sent IPC message to task B
    [experimental task B] activation 10: received IPC message {sender: 1, seq: 10} -- OK
    [experimental task A] activation 11: sent IPC message to task B
    [experimental task B] activation 11: received IPC message {sender: 1, seq: 11} -- OK
    [experimental task A] activation 12: sent IPC message to task B
    [experimental task B] activation 12: received IPC message {sender: 1, seq: 12} -- OK
    [experimental task A] activation 13: sent IPC message to task B
    [experimental task B] activation 13: received IPC message {sender: 1, seq: 13} -- OK
    [experimental task A] activation 14: sent IPC message to task B
    [experimental task B] activation 14: received IPC message {sender: 1, seq: 14} -- OK
    [experimental task A] activation 15: sent IPC message to task B
    [experimental task B] activation 15: received IPC message {sender: 1, seq: 15} -- OK
    [experimental task A] activation 16: sent IPC message to task B
    [experimental task B] activation 16: received IPC message {sender: 1, seq: 16} -- OK
    [experimental task A] activation 17: sent IPC message to task B
    [experimental task B] activation 17: received IPC message {sender: 1, seq: 17} -- OK
    [experimental task A] activation 18: sent IPC message to task B
    [experimental task B] activation 18: received IPC message {sender: 1, seq: 18} -- OK
    [experimental task A] activation 19: sent IPC message to task B
    [experimental task B] activation 19: received IPC message {sender: 1, seq: 19} -- OK
    [experimental task A] activation 20: sent IPC message to task B
    [experimental task B] activation 20: received IPC message {sender: 1, seq: 20} -- OK
    [experimental task A] activation 21: sent IPC message to task B
    [experimental task B] activation 21: received IPC message {sender: 1, seq: 21} -- OK
    [experimental task A] activation 22: sent IPC message to task B
    [experimental task B] activation 22: received IPC message {sender: 1, seq: 22} -- OK
    [experimental task A] activation 23: sent IPC message to task B
    [experimental task B] activation 23: received IPC message {sender: 1, seq: 23} -- OK
    [experimental task A] activation 24: sent IPC message to task B
    [experimental task B] activation 24: received IPC message {sender: 1, seq: 24} -- OK
    [experimental task A] activation 25: sent IPC message to task B
    [experimental task B] activation 25: received IPC message {sender: 1, seq: 25} -- OK
    [experimental task A] activation 26: sent IPC message to task B
    [experimental task B] activation 26: received IPC message {sender: 1, seq: 26} -- OK
    [experimental task A] activation 27: sent IPC message to task B
    [experimental task B] activation 27: received IPC message {sender: 1, seq: 27} -- OK
    [experimental task A] activation 28: sent IPC message to task B
    [experimental task B] activation 28: received IPC message {sender: 1, seq: 28} -- OK
    [experimental task A] activation 29: sent IPC message to task B
    [experimental task B] activation 29: received IPC message {sender: 1, seq: 29} -- OK
    [experimental task A] activation 30: sent IPC message to task B
    [experimental task B] activation 30: received IPC message {sender: 1, seq: 30} -- OK
    [experimental task A] activation 31: sent IPC message to task B
    [experimental task B] activation 31: received IPC message {sender: 1, seq: 31} -- OK
    [experimental task A] activation 32: sent IPC message to task B
    [experimental task B] activation 32: received IPC message {sender: 1, seq: 32} -- OK
    [experimental task A] activation 33: sent IPC message to task B
    [experimental task B] activation 33: received IPC message {sender: 1, seq: 33} -- OK
    [experimental task A] activation 34: sent IPC message to task B
    [experimental task B] activation 34: received IPC message {sender: 1, seq: 34} -- OK
    [experimental task A] activation 35: sent IPC message to task B
    [experimental task B] activation 35: received IPC message {sender: 1, seq: 35} -- OK
    [experimental task A] activation 36: sent IPC message to task B
    [experimental task B] activation 36: received IPC message {sender: 1, seq: 36} -- OK
    [experimental task A] activation 37: sent IPC message to task B
    [experimental task B] activation 37: received IPC message {sender: 1, seq: 37} -- OK
    [experimental task A] activation 38: sent IPC message to task B
    [experimental task B] activation 38: received IPC message {sender: 1, seq: 38} -- OK
    [experimental task A] activation 39: sent IPC message to task B
    [experimental task B] activation 39: received IPC message {sender: 1, seq: 39} -- OK
    [experimental task A] activation 40: sent IPC message to task B
    [experimental task B] activation 40: received IPC message {sender: 1, seq: 40} -- OK
    [experimental task A] activation 41: sent IPC message to task B
    [experimental task B] activation 41: received IPC message {sender: 1, seq: 41} -- OK
    [experimental task A] activation 42: sent IPC message to task B
    [experimental task B] activation 42: received IPC message {sender: 1, seq: 42} -- OK
    [experimental task A] activation 43: sent IPC message to task B
    [experimental task B] activation 43: received IPC message {sender: 1, seq: 43} -- OK
    [experimental task A] activation 44: sent IPC message to task B
    [experimental task B] activation 44: received IPC message {sender: 1, seq: 44} -- OK
    [experimental task A] activation 45: sent IPC message to task B
    [experimental task B] activation 45: received IPC message {sender: 1, seq: 45} -- OK
    [experimental task A] activation 46: sent IPC message to task B
    [experimental task B] activation 46: received IPC message {sender: 1, seq: 46} -- OK
    [experimental task A] activation 47: sent IPC message to task B
    [experimental task B] activation 47: received IPC message {sender: 1, seq: 47} -- OK
    [experimental task A] activation 48: sent IPC message to task B
    [experimental task B] activation 48: received IPC message {sender: 1, seq: 48} -- OK
    [experimental task A] activation 49: sent IPC message to task B
    [experimental task B] activation 49: received IPC message {sender: 1, seq: 49} -- OK
    [experimental task A] activation 50: sent IPC message to task B
    [experimental task B] activation 50: received IPC message {sender: 1, seq: 50} -- OK
    [experimental task A] activation 51: sent IPC message to task B
    [experimental task B] activation 51: received IPC message {sender: 1, seq: 51} -- OK
    [experimental task A] activation 52: sent IPC message to task B
    [experimental task B] activation 52: received IPC message {sender: 1, seq: 52} -- OK
    [experimental task A] activation 53: sent IPC message to task B
    [experimental task B] activation 53: received IPC message {sender: 1, seq: 53} -- OK
    [experimental task A] activation 54: sent IPC message to task B
    [experimental task B] activation 54: received IPC message {sender: 1, seq: 54} -- OK
    [experimental task A] activation 55: sent IPC message to task B
    [experimental task B] activation 55: received IPC message {sender: 1, seq: 55} -- OK
    [experimental task A] activation 56: sent IPC message to task B
    [experimental task B] activation 56: received IPC message {sender: 1, seq: 56} -- OK
    [experimental task A] activation 57: sent IPC message to task B
    [experimental task B] activation 57: received IPC message {sender: 1, seq: 57} -- OK
    [experimental task A] activation 58: sent IPC message to task B
    [experimental task B] activation 58: received IPC message {sender: 1, seq: 58} -- OK
    [experimental task A] activation 59: sent IPC message to task B
    [experimental task B] activation 59: received IPC message {sender: 1, seq: 59} -- OK
    [experimental task A] activation 60: sent IPC message to task B
    [experimental task B] activation 60: received IPC message {sender: 1, seq: 60} -- OK
    [experimental task A] activation 61: sent IPC message to task B
    [experimental task B] activation 61: received IPC message {sender: 1, seq: 61} -- OK
    [experimental task A] activation 62: sent IPC message to task B
    [experimental task B] activation 62: received IPC message {sender: 1, seq: 62} -- OK
    [experimental task A] activation 63: sent IPC message to task B
    [experimental task B] activation 63: received IPC message {sender: 1, seq: 63} -- OK
    [experimental task A] activation 64: sent IPC message to task B
    [experimental task B] activation 64: received IPC message {sender: 1, seq: 64} -- OK
    [experimental task A] activation 65: sent IPC message to task B
    [experimental task B] activation 65: received IPC message {sender: 1, seq: 65} -- OK
    [experimental task A] activation 66: sent IPC message to task B
    [experimental task B] activation 66: received IPC message {sender: 1, seq: 66} -- OK
    [experimental task A] activation 67: sent IPC message to task B
    [experimental task B] activation 67: received IPC message {sender: 1, seq: 67} -- OK
    [experimental task A] activation 68: sent IPC message to task B
    [experimental task B] activation 68: received IPC message {sender: 1, seq: 68} -- OK
  experimental isolated-tasks self-test: C 60 ok / 0 failed, D 60 ok / 0 failed (across 60 switches each, repeated CR3 reload)
  experimental scheduler: task 1 kernel stack 0x14000001000..0x14000005000 (4 pages) has guard page 0x14000000000 unmapped -- OK
  experimental scheduler: task 2 kernel stack 0x14000006000..0x1400000a000 (4 pages) has guard page 0x14000005000 unmapped -- OK
  experimental scheduler: task 3 kernel stack 0x1400000b000..0x1400000f000 (4 pages) has guard page 0x1400000a000 unmapped -- OK
  experimental scheduler: task 4 kernel stack 0x1400001c000..0x14000020000 (4 pages) has guard page 0x1400001b000 unmapped -- OK
  experimental scheduler: task 5 kernel stack 0x14000021000..0x14000025000 (4 pages) has guard page 0x14000020000 unmapped -- OK
  experimental scheduler self-test: all 5 task kernel stacks carry a genuinely unmapped guard page directly below them -- OK
  experimental dex-demo: crypto known-answer -- Adler-32("Wikipedia")=0x11e60398 (expected 0x11e60398) OK, SHA-1("abc") 20-byte digest OK
  experimental dex-demo: parsed a real DEX file (file_size=128, map_entry_count=1, all section counts zero as constructed) -- OK
  experimental dex-demo: Adler-32 checksum + SHA-1 signature verified in-kernel -- OK
  experimental dex-demo: corrupted magic -- parse result Some(BadMagic), correctly rejected: true
  experimental dex-demo: corrupted map self-reference -- parse result Some(NoMapListEntry), correctly rejected: true
  experimental dex-demo: corrupted checksum field -- verify result Some(BadChecksum), correctly rejected: true
  experimental dex-demo self-test: crypto known-answer OK, valid parse+integrity OK, bad-magic rejection OK, map-self-reference rejection OK, checksum rejection OK -- OK
  experimental teardown-demo: space X used (wrote+read 0xdeadc0dedeadc0de -- OK), 10 frames reclaimed on destroy (316 -> 306 in-use, baseline 306) -- fully reclaimed
  experimental teardown-demo: space Y's PML4 (0x36c000) == space X's just-freed PML4 (0x36c000) -- OK, frame genuinely reused
  experimental ring3-scheduler: 3 tasks built (E entry 0x50000000000; F, G pre-loaded with initial trap blobs), target 21 total yields (7 per task)
  experimental ring3-scheduler: task E kernel RSP0 stack 0x1400002c000..0x14000031000 (5 pages) has guard page 0x1400002b000 unmapped -- OK
  experimental ring3-scheduler: task F kernel RSP0 stack 0x14000032000..0x14000037000 (5 pages) has guard page 0x14000031000 unmapped -- OK
  experimental ring3-scheduler: task G kernel RSP0 stack 0x14000038000..0x1400003d000 (5 pages) has guard page 0x14000037000 unmapped -- OK
  experimental ring3-scheduler self-test: all 3 live task kernel stacks carry a genuinely unmapped guard page directly below them -- OK
  experimental ring3-scheduler self-test: task E counter=7 (expected 7) -- OK
  experimental ring3-scheduler self-test: task F counter=7 (expected 7) -- OK
  experimental ring3-scheduler self-test: task G counter=7 (expected 7) -- OK
  experimental ring3-scheduler self-test: 21 real trap-based yields processed, all 3 tasks' full register + address-space state correctly suspended and resumed -- OK
  experimental ring3-scheduler teardown: task F's address space (pml4 0x381000, active for its own 7 trap-based yields) destroyed after suspension -- 15 frames reclaimed (357 -> 342 in use) -- OK
  experimental ring3-scheduler teardown: LIVE slot hand-back -- destroyed task F's RSP0 slot (base 0x14000032000, guard 0x14000031000) re-handed out byte-identically as task H's live RSP0 stack, re-verified through H's page tables; task I bumped a fresh slot instead -- OK, the teardown path feeds the exact-count pool
  experimental ring3-scheduler: task H kernel RSP0 stack 0x14000032000..0x14000037000 (5 pages) has guard page 0x14000031000 unmapped -- OK
  experimental ring3-scheduler: task I kernel RSP0 stack 0x1400003e000..0x14000043000 (5 pages) has guard page 0x1400003d000 unmapped -- OK
  experimental ring3-scheduler self-test: all 2 preemption-proof task kernel stacks carry a genuinely unmapped guard page directly below them -- OK
  experimental ring3-scheduler: timer preemption armed -- entering task H, whose program never traps; IRQ0 is the only reschedule point from here (target 6 switches)
  experimental ring3-scheduler preemption self-test: task H counter=25109592 (>0 required -- its program contains no trap instruction, so only a timer switch could ever let it run) -- OK
  experimental ring3-scheduler preemption self-test: task I counter=35367765 (>0 required -- its program contains no trap instruction, so only a timer switch could ever let it run) -- OK
  experimental ring3-scheduler preemption self-test: task H force-switched off the CPU 3 times, task I 3 times (6 timer-driven switches, target 6; 0 cooperative yields during the phase) -- OK
  experimental ring3-scheduler preemption self-test: two non-yielding ring-3 tasks round-robined purely by IRQ0 -- timer-driven preemption, not cooperative-only -- OK
  experimental pie-demo: GNU_RELRO region 0x60000002ef0..0x60000003000 is back to read-only after relocation (loader grant/revoke cycle completed) -- OK
  experimental pie-demo: ET_DYN loaded at base 0x60000000000, entry 0x60000001000 (bias correctly applied by the loader, not baked into the file)
  experimental pie-demo self-test: relocated pointer dereferenced to 0xfeedface12345678 (expected 0xfeedface12345678) -- OK, relocation correctly applied
  experimental pie-demo relro check: page 0x60000002ef0 PTE flags writable=false -- OK
  experimental pie-demo self-test: all 1 GNU_RELRO page(s) verified read-only in the live page tables after relocation -- OK
  experimental elf-dyn-demo: GNU_RELRO region 0x64000002ef0..0x64000003000 revoked read-only after grant/relocate (the GRANT half ran for the first time) -- OK
  experimental elf-dyn-demo: resolved by name via the DT_HASH walk -- dyn_payload 0x64000001000, dyn_value 0x64000003000, dyn_timestamp 0x64000003010 -- OK
  experimental elf-dyn-demo: absent-name lookup returned the typed SymbolNotFound (not a parse error) -- OK
  experimental elf-dyn-demo: GLOB_DAT slot 0x64000002fe0 (in-RELRO .got) == 0x64000003000 (resolved dyn_value) -- OK, symbol-resolved relocation applied correctly
  experimental elf-dyn-demo: R_X86_64_64 slot 0x64000003008 (writable .data) == 0x64000003000 (resolved dyn_value) -- OK, symbol-resolved relocation applied correctly
  experimental elf-dyn-demo: RELRO page 0x64000002000 is read-only and user-readable after the grant/revoke cycle -- OK
  experimental elf-dyn-demo: GLOB_DAT slot residency check: 0x64000002fe0 inside RELRO region 0x64000002ef0..0x64000003000 = true (true expected -- ld placed .got in the GNU_RELRO region; reads are legal, writes protected) -- OK
  experimental elf-dyn-demo: wrote 0x5eed0001 through resolved dyn_timestamp (0x64000003010) and read it back -- OK, a resolved runtime address is a real mapped address
  experimental elf-dyn-demo: kernel-side proof complete; entering dyn_payload (ring 3) which dereferences its own resolved GOT slot
  experimental elf-dyn-demo self-test: the .so's own code read 0xd1ce00f00d through its resolved GOT slot (expected 0xd1ce00f00d) -- OK
  experimental cross-demo: provider's GNU_RELRO region 0x6c000001f40..0x6c000002000 revoked read-only (revoke-only; its layout has no in-region relocations) -- OK
  experimental cross-demo: provider registered (registry 1 object(s)); cross_value 0x6c000002000, dup_symbol 0x6c000002008 resolve from the provider's own tables -- OK
  experimental cross-demo: dependent's GNU_RELRO region 0x6c000042ee8..0x6c000043000 revoked read-only; the grant half ran for both in-region GLOB_DAT slots (one UNDEF-but-resolved) -- OK
  experimental cross-demo: the app's own by-name lookup of cross_value refused with UndefinedSymbol -- OK, the app genuinely does not define it
  experimental cross-demo: app's UNDEF GLOB_DAT slot 0x6c000042fe0 == 0x6c000002000 (the PROVIDER's cross_value, via the registry) -- OK, cross-object relocation applied correctly
  experimental cross-demo: app's duplicate-name GLOB_DAT slot 0x6c000042fd8 == 0x6c000043000 (ITS OWN dup_symbol) -- OK, self-priority: the app's OWN dynsym entry won, not the registry's earlier same-named entry
  experimental cross-demo: registry search order decides -- [app,lib] -> 0x6c000043000 (app), [lib] -> 0x6c000002008 (lib) -- OK, first DEFINED match in order wins
  experimental cross-demo: absent-name registry lookup returned Ok(None) (typed absent, no parse error) -- OK
  experimental cross-demo: the same dependent WITHOUT a resolver refused with Ok(relro = Denied) -- the caller must refuse -- OK, the refusal is NOT gone now that resolution exists
  experimental cross-demo: kernel-side proof complete; entering cross_payload (ring 3), which dereferences its registry-resolved GOT slot
  experimental cross-demo self-test: the dependent's own code read 0xace5f00dd1ce5eed through its cross-resolved GOT slot (expected 0xace5f00dd1ce5eed, the provider's cross_value) -- OK
  experimental full-demo: provider loaded; DT_SONAME fact (crosslib.so.1) validated -- OK
  experimental full-demo: same-SONAME V2 provider refuses the VERS_1.0 requirement with VersionNotProvided -- OK, version matching is enforced; the name alone is NOT enough
  experimental full-demo: dependent's GNU_RELRO region 0x6c000202e68..0x6c000203000 revoked read-only after the version-checked grant/relocate -- OK
  experimental full-demo: dependent's versioned GLOB_DAT slot 0x6c000202fe0 == 0x6c000003000 (provider's cross_value, version-checked) -- OK
  experimental full-demo: dependent's duplicate-name slot 0x6c000202fd8 == 0x6c000203008 (its OWN dup_symbol) -- OK, self-priority held under the versioned provider too
  experimental full-demo: lazy JUMP_SLOT slot 0x6c000203000 == 0x6c000210000 (trap stub armed; NOT eagerly resolved) -- OK
  experimental full-demo: empty-registry dependent refused with MissingDependency -- OK, the declared dependency contract is enforced
  experimental full-demo: V2-registry dependent refused (denied region or typed version error) -- OK, the version contract refuses end to end
  experimental full-demo: lazy-bind service probe of 0x6c000210000 while the registry was EMPTY refused with the typed ServiceEmpty -- OK, the empty branch proven kernel-side before the chain's first registration
  experimental full-demo self-test: cross_value via versioned slot = 0xace5f00dd1ce5eed (expected 0xace5f00dd1ce5eed) -- OK
  experimental full-demo self-test: cross_fn results (eax) 0xfa571ae5 / 0xfa571ae5 (expected 0xfa571ae5 both; first via lazy bind, later direct) -- OK
  experimental full-demo self-test: lazy-bind trap fired 1 time(s) across 3 calls (exactly 1 required) -- OK, binding is lazy; later calls are direct
  experimental full-demo: shared lazy-bind service: 1 object(s) registered so far, 1 bind(s) served through ONE vector (0x88) this boot -- OK, this dependent binds through the service, not a demo static
  experimental deps-demo: GNU walk of a name in NEITHER of A's tables refused with SymbolNotFound -- OK (absent, not malformed: GNU-absent is typed as absent)
  experimental deps-demo: provider A carries BOTH hash tables (DT_HASH authority + DT_GNU_HASH); GNU walk == SysV walk on cross_value (0x6d000003000) -- OK, the GNU mechanism's answer matches the SysV mechanism's
  experimental deps-demo: provider A loaded (crosslib.so.1) -- OK
  experimental deps-demo: pre-overlay lookup of interp_mark = absent (provider A does not define it) -- OK, absent before the overlay exists
  experimental deps-demo: after interpose(overlay), lookup of interp_mark = 0x6d000103000 (data 0x1111222233334444, the overlay's value) -- OK, interposition overrides load order
  experimental deps-demo: looping catalog refused with DependencyCycle -- OK, a file can never load itself to satisfy itself
  experimental deps-demo: DT_NEEDED CHAIN (dependent -> crosslib2.so.1 -> crosslib3.so.1 -> crosslib4.so.1 -> crosslib.so.1) satisfied dependency-first; 3 catalog object(s) auto-loaded -- OK, the DEPTH-3 chain (B, C and D) loaded from the catalog; A was already in scope
  experimental deps-demo: depth-3 chain under a catalog whose cap is 2 refused with DependencyDepthExceeded (at the D hop, B and C mapped, A never reached) -- OK
  experimental deps-demo: 3 auto-loaded chain providers carry their DISTINCT marker immediates at their own bases (3/3 symbols found, contents == the source constants) -- OK, the right file loaded at the right base, per object
  experimental deps-demo: dependent's GNU_RELRO revokes read-only WITH an unresolved WEAK entry in-region (weak != denial) -- OK
  experimental deps-demo: dependent's versioned GLOB_DAT slot 0x6d000302fe0 == 0x6d000003000 (provider's cross_value as resolved BY THE CATALOG CHAIN) -- OK
  experimental deps-demo: dependent's interp_mark slot 0x6d000302fd8 == 0x6d000103000 (the INTERPOSITION overlay's address, not that of anything provider A defines) -- OK
  experimental deps-demo: WEAK UNDEF ghost_fn@VERS_1.0 slot 0x6d000302fd0 == 0x0 (the NULL slot; unresolved weak stays non-fatal) -- OK
  experimental deps-demo: lazy JUMP_SLOT slot 0x6d000303000 == 0x6d000310000 (trap stub armed on THIS object's own trampoline page; NOT eagerly resolved) -- OK
  experimental deps-demo: WEAK version-flag artifact resolves cross_value@VERS_1.0 against the providing scope (weak relaxes only the UNRESOLVED case) -- OK
  experimental deps-demo: GNU walk of the weak artifact's UNDEF cross_value reports ABSENT (SymbolNotFound) while the SysV walk reports UndefinedSymbol -- OK (GNU excludes UNDEF entries from its buckets; DT_HASH chains them; the asymmetry is typed)
  experimental deps-demo: WEAK version-flag requirement with unmet VERS_1.0 takes the NULL slot (0), load proceeds, relro read-only -- OK (a STRONG requirement is the typed VersionNotProvided refusal in the same position)
  experimental deps-demo: lazy-bind service refuses an UNREGISTERED trampoline page (0x6d000570000) with the typed UnregisteredTrampoline carrying that page; registry size 1 -> 1 and binds 1 -> 1 unchanged -- OK (the handler's panic path is demonstrated, not assumed)
  experimental deps-demo self-test: cross_value = 0xace5f00dd1ce5eed (expected 0xace5f00dd1ce5eed) -- OK
  experimental deps-demo self-test: interp_mark = 0x1111222233334444 (expected 0x1111222233334444, the overlay's value read through ring 3) -- OK
  experimental deps-demo: lazy-bind service fired 1 time(s) across 3 calls (exactly 1 required) -- OK, binding is lazy; calls 2-3 are direct
  experimental deps-demo: shared lazy-bind service: 2 independent object(s) registered, 2 bind(s) served through ONE vector (0x88) this boot -- OK, the vector is a SERVICE, not a demo
  experimental plt-demo: provider3 loaded at 0x6d000770000: DT_SONAME pltprov.so.1 AND its prov_marker data reads back 0xe11cede1e1e1e1e -- OK, the RIGHT file at the RIGHT base
  experimental plt-demo: registry probe of link-map id 0xbeefc0de (no GNU object registered yet) refused with the typed UnregisteredLinkMap carrying THAT id -- OK, an unregistered cookie is refused, never guessed
  experimental plt-demo: instance 1 own-.plt armed on THE OBJECT's own .got.plt (0x6d000702fe8): GOT[1] = link-map id 0x1, GOT[2] = resolver thunk 0x6d000710000 (1 .rela.plt entries) -- OK, the GNU resolver pointers are in place (JUMP_SLOT slots touched only by the GNU lazy bias relocation)
  experimental plt-demo: instance 1 JUMP_SLOT idx 0 slot 0x6d000703000 == 0x6d000701016 pre-run (the LINKER's own plt+6 initial value with the GNU lazy relocation bias applied; not resolved; expected live value 0x6d000661000) -- OK
  experimental plt-demo: instance 1 registered under link-map id 0x1; the shared service now holds 3 object(s) -- OK, the GNU path lives in the SAME registry the trap-stub path uses
  experimental plt-demo self-test: instance 1 -- cross_value = 0xace5f00dd1ce5eed (expected 0xace5f00dd1ce5eed) -- OK
  experimental plt-demo self-test: instance 1 -- cross_fn results (eax) 0xfa571ae5 / 0xfa571ae5 / 0xfa571ae5 (expected 0xfa571ae5 all; call 1 entered the callee DIRECTLY via the resolver's guided return) -- OK
  experimental plt-demo self-test: instance 1 -- its REAL .got.plt slot 0x6d000703000 patched to the resolved cross_fn (0x6d000661000) -- OK
  experimental plt-demo self-test: instance 1 -- first-use binds for ITS OWN cookie 0x1: 1 across 3 calls (exactly 1 required) -- OK, GNU lazy semantics on THIS object's own .plt machinery
  experimental plt-demo: instance 2 own-.plt armed on THE OBJECT's own .got.plt (0x6d000742fe8): GOT[1] = link-map id 0x2, GOT[2] = resolver thunk 0x6d000750000 (1 .rela.plt entries) -- OK, the GNU resolver pointers are in place (JUMP_SLOT slots touched only by the GNU lazy bias relocation)
  experimental plt-demo: instance 2 JUMP_SLOT idx 0 slot 0x6d000743000 == 0x6d000741016 pre-run (the LINKER's own plt+6 initial value with the GNU lazy relocation bias applied; not resolved; expected live value 0x6d000661000) -- OK
  experimental plt-demo: instance 2 registered under link-map id 0x2; the shared service now holds 4 object(s) -- OK, the GNU path lives in the SAME registry the trap-stub path uses
  experimental plt-demo self-test: instance 2 -- cross_value = 0xace5f00dd1ce5eed (expected 0xace5f00dd1ce5eed) -- OK
  experimental plt-demo self-test: instance 2 -- cross_fn results (eax) 0xfa571ae5 / 0xfa571ae5 / 0xfa571ae5 (expected 0xfa571ae5 all; call 1 entered the callee DIRECTLY via the resolver's guided return) -- OK
  experimental plt-demo self-test: instance 2 -- its REAL .got.plt slot 0x6d000743000 patched to the resolved cross_fn (0x6d000661000) -- OK
  experimental plt-demo self-test: instance 2 -- first-use binds for ITS OWN cookie 0x2: 1 across 3 calls (exactly 1 required) -- OK, GNU lazy semantics on THIS object's own .plt machinery
  experimental plt-demo: instance 3 own-.plt armed on THE OBJECT's own .got.plt (0x6d000782fe8): GOT[1] = link-map id 0x3, GOT[2] = resolver thunk 0x6d000790000 (2 .rela.plt entries) -- OK, the GNU resolver pointers are in place (JUMP_SLOT slots touched only by the GNU lazy bias relocation)
  experimental plt-demo: instance 3 JUMP_SLOT idx 0 slot 0x6d000783000 == 0x6d000781016 pre-run (the LINKER's own plt+6 initial value with the GNU lazy relocation bias applied; not resolved; expected live value 0x6d000771010) -- OK
  experimental plt-demo: instance 3 JUMP_SLOT idx 1 slot 0x6d000783008 == 0x6d000781026 pre-run (the LINKER's own plt+6 initial value with the GNU lazy relocation bias applied; not resolved; expected live value 0x6d000771000) -- OK
  experimental plt-demo: instance 3 registered under link-map id 0x3; the shared service now holds 5 object(s) -- OK, the GNU path lives in the SAME registry the trap-stub path uses
  experimental plt-demo self-test: instance 3 (multi-entry) -- fn_a = 0xa11ce0de (expected 0xa11ce0de), fn_b = 0xbb0b5e11 (expected 0xbb0b5e11, TWO calls, second direct) -- OK
  experimental plt-demo self-test: instance 3 -- .rela.plt idx 0 slot 0x6d000783000 patched to 0x6d000771010 (expected 0x6d000771010, ITS OWN index) -- OK
  experimental plt-demo self-test: instance 3 -- .rela.plt idx 1 slot 0x6d000783008 patched to 0x6d000771000 (expected 0x6d000771000, ITS OWN index) -- OK
  experimental plt-demo self-test: MULTI-ENTRY artifact cookie 0x3: 2 first-use binds (EXACTLY 2 -- one per .rela.plt entry, one cookie); shared service now 5 object(s) / 6 bind(s) across both gates (0x88 trap stubs + 0x8B GNU own-.plt) -- OK, one registry serves a MULTI-ENTRY GNU object AND the single-entry AND trap-stub paths
  experimental plt-demo: GNU OWN-.plt mode requested on the PLT-LESS artifact REFUSED with the typed MalformedDynamicTables (no .got.plt machinery to arm) -- OK, the protocol refuses cleanly where its machinery does not exist
  experimental plt-demo: the SAME PLT-LESS artifact loads eagerly: its GLOB_DAT slot 0x6d0007b2fe0 == provider A's cross_value (0x6d000663000) resolved AT LOAD, relro revoked read-only, NO GNU arming side effects (registry 5 -> 5, binds 6 -> 6) -- OK, the GNU protocol is opt-in per load; eager stays the default for PLT-less objects
  experimental pe-demo: PE32+ loaded at its own ImageBase (no relocation), entry 0x140001000
  experimental pe-demo self-test: PE entry point wrote 0xfeedc0defeedc0de (expected 0xfeedc0defeedc0de) -- OK, a genuine Windows-format (PE32+) executable's own code ran in ring 3
  experimental pe-reloc-demo: PE32+ loaded at 0x90000000000 (its ImageBase is 0x140000000); .data pointer relocated to 0x90000002008 (expected 0x90000002008)
  experimental pe-reloc-demo self-test: relocated pointer wrote 0xfeedfacecafebeef (expected 0xfeedfacecafebeef) -- OK, a relocatable PE32+ binary ran correctly at a loader-chosen address
  experimental pe-import-demo: provider loaded at its own ImageBase 0x180000000000; its export table declares "userprog_pe_import_dll.dll"
  experimental pe-import-demo: export table facts -- 2 name(s), lookup(crossfn) = provider+0x1000, lookup(crossmark) = provider+0x2000
  experimental pe-import-demo: provider export lookup self-test: crossfn 0x180000001000, crossmark 0x180000002000, absent symbol None -- OK
  experimental pe-import-demo: forwarder host loaded at its own ImageBase 0x1c0000000000; its export table declares "userprog_pe_import_fwdprov.dll" with 3 name(s), every EAT entry a directory-internal forwarder -- OK
  experimental pe-import-demo: negative control 1 -- EMPTY registry refused the import walk with the typed MissingDllImport -- OK
  experimental pe-import-demo: negative control 2 -- the DLL name bound to the WRONG FILE's empty export table refused with the typed MissingImport -- OK, the registry identity is the file, not the name
  experimental pe-import-demo: negative control 3 -- a SELF-CYCLING forwarded export refused at the depth cap with the typed ForwarderDepthExceeded -- OK
  experimental pe-import-demo: negative control 4 -- a forwarded export naming an UNREGISTERED DLL refused with the typed MissingDllImport -- OK
  experimental pe-import-demo: ordinal out-of-range / empty-table probes answer typed-None, never a guess -- OK
  experimental pe-import-demo: negative control 5 -- a fresh-stamped descriptor with an ALL-FRESH forwarder ref whose IAT slot contradicts the chased answer refused with the typed BoundValueCorrupt (0xdeadbeef00001000 != 0x180000001000) -- OK, stamps are verified THROUGH the forwarder chains too, never trusted blindly
  experimental pe-import-demo: negative control 6 -- the REFSTALE carrier (one ref stamp byte beyond the bad client's) DISCARDED the same wrong slot (0xdeadbeef00001000) and resolved it fresh through the same chase (0x180000001000); the descriptor's own stamp stayed fresh (0x386d4380) and the ref declared 0x386d4381 -- OK, one ref stamp byte decides refuse-vs-discard
  experimental pe-import-demo: negative control 7 -- the REF2BAD carrier (TWO refs, BOTH FRESH) refused through the two-ref chain with the typed BoundValueCorrupt (0xdeadbeef00001000 != 0x180000001000) -- OK, the count made the walk unambiguous and the fresh surface still verified the slot THROUGH both refs
  experimental pe-import-demo: negative control 8 -- the REF2STALE carrier (same two refs, only the SECOND's stamp moved to 0x386d4381) DISCARDED the same wrong slot (0xdeadbeef00001000) and resolved fresh through the same chase (0x180000001000); the first ref stayed fresh (0x386d4380) -- OK, a LATER ref stamp byte decides refuse-vs-discard across a TWO-ref chain
  experimental pe-import-demo: negative control 9 (positive half) -- the DELAYGOOD carrier's two delay imports eagerly resolved through the same registry (named crossfn -> 0x180000001000, ordinal crossmark -> 0x180000002000; prior contents were the composed unbound thunks, both slots WRITTEN and read back) and the module handle 0x180000000000 was written where the descriptor pointed -- OK, the eager delay ruling matches the normal walk's answers
  experimental pe-import-demo: negative control 10 -- the DELAYBAD carrier's delay descriptor naming 'nowhere.dll' refused with the typed MissingDelayDllImport -- OK, the name-vs-file ruling applies to delay descriptors with their OWN flavor
  experimental pe-import-demo: negative control 11 -- the DELAYMUT carrier (dwTimeStamp = provider stamp + 1, the OLD-BIND marker) refused with the typed MalformedDelayImport -- OK, a binding guarantee the kernel cannot verify is NEVER silently honored and never silently ignored
  experimental pe-import-demo: negative control 12 -- the DELAYATTRS carrier (grAttrs = 0x3, dlattrRva plus an UNDEFINED bit) refused with the typed MalformedDelayImport -- OK, an attribute set the Windows helper itself cannot interpret is refused before any table is trusted
  experimental pe-import-demo: negative control 13 -- the DELAYMIS carrier (INT[0] names crossfn while IAT[0] carries an ordinal thunk) refused with the typed MalformedDelayImport -- OK, the unbound pre-state the walk resolves is checked entry-for-entry, never assumed
  experimental pe-import-demo: negative control 14 -- the DELAYVA carrier (grAttrs = 0, the pre-VC7 VA form, with RVA-sized fields) refused with the typed MalformedDelayImport -- OK, a VA-form field that cannot be a VA of this image is refused, not reinterpreted
  experimental pe-import-demo: negative control 15 -- the DELAYVABIAS carrier (real VA-form descriptor of ImageBase 0x400000, loaded at 0x460000000000) refused with the typed MalformedDelayImport -- OK, a pre-VC7 descriptor's baked VAs are refused in a relocated image instead of being reinterpreted against the wrong base
  experimental pe-import-demo: negative control 16 -- the DELAYMANY carrier's 17th descriptor refused with the typed MalformedDelayImport; descriptors 0-15 are FULLY resolved (their IAT slots hold the provider's crossfn 0x180000001000, their handle slots the provider's mapped base), while the 17th's slot still holds its composed unbound thunk 0x276b and its handle slot is untouched -- OK, the 16-descriptor cap is EXCLUSIVE: the legal maximum is resolved in full and only the entry past it is refused
  experimental pe-import-demo: negative control 17 -- the DELAYTHUNKS carrier's 65th thunk entry refused with the typed MalformedDelayImport; entries 0-63 are FULLY resolved (all 64 IAT slots hold the provider's crossfn 0x180000001000) while entry 64 still holds its composed unbound thunk 0x271b -- OK, the 64-entry thunk cap is EXCLUSIVE for the same reason, and one descriptor's INT/IAT pair was walked end to end before the refusal
  experimental pe-import-demo: negative control 18 -- the DELAYFULL carrier (EXACTLY 16 descriptors, the FIRST carrying EXACTLY 64 thunk entries, 79 imports) ACCEPTED and resolved completely: every slot holds the provider's crossfn 0x180000001000, all 16 handle slots the provider's base, and the IAT terminator right after the 64th entry stopped the walk cleanly -- OK, both caps are exclusive bounds: the legal maxima are accepted, one past them is refused
  experimental pe-import-demo: BOUND-IMPORT facts -- provider's fresh-stamp entry: 2 slot(s) HONORED (prefills verified == fresh resolution, slots left un-written); the stale entry (stamp = provider's + 1, a stamp no file carries): its pre-bound contradiction 0x180000002000 DISCARDED and resolved fresh to 0x180000001000; nobody.dll's inert entry parsed and ignored -- OK
  experimental pe-import-demo: resolved 3 import(s) through the REAL import directories -- OK: named crossfn -> 0x180000001000 and ordinal-form crossmark (`ord2`) -> 0x180000002000 both HONORED by stamp (verified prefills, left un-written); forwarder crossfn_fwd -> 0x180000001000 (the PROVIDER's .text, chased through the forwarder host's export entry after its stale pre-binding was DISCARDED); every slot reads back its (verified or written) value
  experimental pe-import-demo: IAT slots -- crossfn 0x180000001000, ordinal crossmark 0x180000002000, forwarder crossfn_fwd 0x180000001000 (slot 21990232563816; slot 21990232563824; slot 21990232563840) -- OK
  experimental pe-import-demo: resolved crossmark address dereferences to 0xace5f00dd1ce5eed (expected 0xace5f00dd1ce5eed, the provider's own .data immediate) -- OK
  experimental pe-import-demo: kernel-side proof complete; entering the imported client (ring 3) which dereferences crossmark through its resolved IAT slot and calls crossfn through its own
  experimental pe-import-demo self-test: imported data read 0xace5f00dd1ce5eed through the resolved IAT slot (expected 0xace5f00dd1ce5eed); imported function returned 0xfa571ae5 (expected 0xfa571ae5); forwarder-resolved import returned 0xfa571ae5 (expected 0xfa571ae5, the PROVIDER's own code executed through the forwarder host's export entry) -- OK, a real Windows-format executable's own imports -- named, ordinal, and forwarder-chased -- all resolved through real Windows-format export tables (GetProcAddress semantics), and its own code used them in ring 3
  experimental isolation: address space A built (pml4 0x4fd000), program A loaded, entry 0x20000000000
  experimental isolation: trap from process A (cpl=3), read 0xc0ffee (expected 0xc0ffee) -- OK
  experimental isolation: address space B built (pml4 0x508000), program B loaded, entry 0x20000000000 -- switched active, A is now inactive but preserved
  experimental isolation: trap from process B (cpl=3), read 0xbbbbbbbb (expected 0xbbbbbbbb) -- OK
  experimental isolation: switched back to process A's address space, re-read 0xc0ffee (expected 0xc0ffee, unchanged since A ran) -- OK, ISOLATED
  experimental isolation self-test: two independent address spaces verified mutually isolated -- OK
  memory stack-reuse self-test: released 2-page slot re-handed out byte-identically (base 0x14000092000, guard 0x14000091000, top 0x14000094000) fully re-verified with its guard unmapped; an 8-page release absorbed WHOLE by 3-page and by 2-page requests (same guard/top, descriptor stays 8 pages, zero splits); a bigger request never touched a smaller pooled slot -- OK, exact-match preferred, whole-slot absorption bounded, no rewind
  memory guard-demo: stack mapped at 0x1400009e000..0x140000a2000 (4 pages); bottom present OK, top present OK, guard page 0x1400009d000 absent OK, page above 0x140000a2000 absent OK
  memory guard-demo self-test: guarded stack correctly built (4 usable pages, page below genuinely unmapped, page above genuinely unmapped) -- OK
  memory guard-demo: arming terminal overflow probe -- writing one qword at the guard page 0x1400009d000 (expect a PROTECTION_VIOLATION page fault)
  memory guard-demo self-test: overflow write to guard page 0x1400009d000 faulted exactly as required (accessed 0x1400009d000, error code PageFaultErrorCode(CAUSED_BY_WRITE)) -- OK, a stack overflow is caught deterministically instead of corrupting memory below it
nyrqis kernel: phase 1+2+3+4+ring3-elf-isolation-poc+guard-pages complete
```

## What does NOT exist yet (do not assume otherwise)

**2026-10-15 (eighth round) — the GNU hash table as a REAL second mechanism, per-stage stack ownership, a depth-2 dependency chain, and the weak VERNEED flag proven on a real artifact:**

- **DT_GNU_HASH lookups, glibc-style** (`elf::DynTables::lookup_gnu_hash` + `elf::lookup_symbol_gnu`): DT_GNU_HASH is no longer an unparsed stub — the table is captured, and the lookup is the genuine glibc mechanism, a SEPARATE mechanism from the SysV walk, not a re-skin: the GNU multiplicative hash (DJBX33A, init 5381 ×33) — NOT the SysV ELF hash the code originally (wrongly) reused for it; the two-bit bloom filter, the symoffset-bounded buckets with the GNU-hash-ordered chains, and per-chain low-bit end markers, every hop bounds-checked, bloom miss typed as `SymbolNotFound` (absent) and structural corruption as `MalformedDynamicTables`, never confused. Two documented, verified asymmetries between the mechanisms made this perf-not-semantics only with care: (a) DT_GNU_HASH EXCLUDES undefined symbols from its buckets (confirmed against the actual artifacts), so `lookup_symbol`'s "name present but UNDEF → `UndefinedSymbol`" contract needs the SysV re-walk when GNU says absent; (b) DT_HASH remains the AUTHORITY over the symbol count and the absence arbiter — every artifact in this repo is built `--hash-style=both`, so the resolution paths prefer GNU, and the deps demo pins the equality every boot: GNU walk == SysV walk on the SAME both-tables artifact (`cross_value` → same address). What this does NOT pretend: a userland `ld.so`-grade .plt resolver (a listed Phase-5 remainder, unchanged; on the .plt-resolver side the binder scripts the fix-up, see the seventh round).
- **GNU==SysV answer-equality + demo interfaces**: the two public per-mechanism functions ([`lookup_symbol_sysv`] / [`lookup_symbol_gnu`]) and the combined GNU-preferred `lookup_hash_any` all serve real resolution paths; the deps demo pins the equality every boot — `carries_gnu_hash()` (the file carries BOTH tables, set by `--hash-style=both`) and both walkers' returned addresses compared on the same name. The equality check is a real crown trial, not a tautology: it caught BOTH of this round's root bugs on the first boots — the digit-swapped `DT_GNU_HASH` tag constant (`0x6fffffef` vs readelf-pinned `0x6ffffef5`) and the original walker's reuse of the SysV hash function — and would catch them again. The walkers' REFUSALS are typed facts too, pinned every boot: a name in neither of provider A's tables answers `SymbolNotFound` through the GNU walk (absent, not malformed), and the weak artifact's UNDEF `cross_value` — present in dynsym and the DT_HASH chain, excluded from the GNU bloom/buckets — answers ABSENT by GNU while the SysV walk reports it `UndefinedSymbol`, exactly the asymmetry `lookup_symbol`'s documented SysV re-walk preserves.
- **Per-stage stack ownership** (`experimental::chain::run_on_fresh_stack`): the demo chain's `on_trap` continuations never return, so WITHOUT this, each demo's kernel-side frames accumulate on ONE kernel stack across the whole boot — the original reason `DEFAULT_STACK_PAGES` grew 5→8. This fixes the cause, not the budget: each chained demo now jumps onto ITS OWN address space's guarded RSP0 stack and runs its stage there, and the shared default stays 5 pages. The deps demo — the chain's deepest — completes the round by SPLITTING its own stage into two phases: phase A (the deep loader work: GNU facts, interposition, the depth-3 tree, the weak controls) and phase B (map/arm/enter ring 3), each on its own fresh stack, phase B on the shared DEFAULT budget. The split was then MEASURED at the boots, loudly: phase A at 6 and at 7 pages faulted a #PF→#DF at the guard on the tree load (zero corruption, twice, exactly what the guard exists for) and holds 8 — the honest per-phase allocation, declared via `AddressSpace::new_sharing_kernel_with_stack_pages`, not a silent shared bump. The seam deliberately does NOT switch CR3: everything the payload needs (its own mappings, the loaded providers' tables, the registry) lives under phase A's private PML4 subtree (only indices 0 and 2 are shared), so the split is a STACK split — `TSS.RSP0` moves to phase B's fresh shared-region stack and `run_on_fresh_stack` jumps, while the address-space context stays phase A's. What is shared stays shared; what is private stays private; and the guard keeps every page-count claim checked at every boot.
- **Depth-3 dependency chain realized as a fact**: after provider C (`crosslib3.so.1`, real SONAME) and provider D (`crosslib4.so.1`) joined, and each upstream link was RE-LINKED to record its edge at build time (B's link line against C, C's against D, D's against the first provider), the chain is now `userprog_deps.elf` NEEDs `crosslib2.so.1` NEEDs `crosslib3.so.1` NEEDs `crosslib4.so.1` NEEDs `crosslib.so.1`: the catalog auto-load count is checked at == 3 (B, C and D auto-loaded from catalog; A was pre-registered), so the loader's recursion depth has grown one hop per round with the check retargeted each time. TWO further per-object facts close the round: (a) each auto-loaded provider carries a DISTINCT marker immediate (its own `cross*_marker` symbol, contents pinned against the source constants at ITS OWN loaded base — the right file at the right base, not a count alone); (b) the loader's OTHER typed refusal, `DependencyDepthExceeded`, is now a checked negative control like the cycle refusal: the same depth-3 chain under a catalog whose declared cap is 2 refuses at the D hop (B and C mapped into scratch bases, A never reached) every boot.
- **The weak VERNEED flag on a REAL artifact** (`tools/patch_weak_verneed.py` + `userprog_weak.elf`): the seventh round proved the weak SYMBOL-side rule and stated honestly that no artifact carried `VER_FLG_WEAK`; this round closes the FLAG half, composed deliberately rather than simulated: the environment's GNU ld refuses to set `VER_FLG_WEAK` even when every bind in the dependency is weak (this round's author verified the behavior empirically with readelf before accepting the gap), so `build.sh` links the WEAK-ONLY artifact (`userprog_weak.elf`, its ONLY undefined symbol a weak bind of the versioned `cross_value`) and then a checked Python step patches `VER_FLG_WEAK` onto the vernaux entry, self-verifying the patch via readelf and aborting the build when the flag is not visible to readelf. The gate refuse the build unless the file carries `Flags: WEAK`. The kernel-side driver runs both controls (the POSITIVE control: the weak requirement against the providing scope resolves normally — weak relaxes only the UNRESOLVED case; the NEGATIVE control: the weak requirement against the WRONG-version scope takes the NULL slot, the load proceeds, RELRO still revokes — the seventh round's strong-refusal is unchanged under the same conditions) and both checks the walker-taxonomy asymmetry facts below. The composition is disclosed, not simulated toolchain behavior; a toolchain that emits the flag naturally passes through as a verified no-op. The patcher is also IDEMPOTENT, verified by a real second run: a file already carrying `Flags: WEAK` triggers no re-write (a lazy old|or re-patch could not corrupt the aux entry, but neither run costs anything, so the no-op is explicit, self-checking and reported).
- **Per-dynsymbol slot facts by SYMBOL + the demo authority**: the eighth round follows the seventh's rule: demo-side facts are located by SYMBOL (via the version-aware tables) so they survive a layout-tool drift; the static readelf pins live in the CI gate.
- **The lazy-binder refusal as a NEGATIVE CONTROL, not an assumption** (`experimental::lazy_binder::identify` + `probe_unregistered`): the 0x88 handler's behavior on a trampoline page NO registered object owns was a documented loud panic; the round split the identification into one shared typed path (`LazyBindRefusal::UnregisteredTrampoline { ip_page }` / `ServiceEmpty`) and gave it a probe so the deps demo can check the refusal WITHOUT killing the boot: the typed variant carries the probe page back, the registry size and bind totals stay frozen, and the page has zero binds. The handler and the probe consult the SAME decision (`identify`), so "the handler would panic exactly here" is a demonstrated fact, not a reader's inference.
- **The guarded-stack region's first slot-reuse mechanism** (`memory::kernel_stack::release` + reuse pool): the region was a documented allocate-forever bump. It still is for LIVE stacks (nothing running releases its own stack until the teardown story exists), but release() now unmaps a completed stack's pages, frees the backing frames through the same deallocation path `AddressSpace::destroy` uses, and returns the slot to the reuse pool; `allocate` re-hands out a released slot of the same page count byte-identically — same guard position, same top — instead of bumping, and (superseding the never-split wording of the earlier increments, below) a SMALLER request absorbs the smallest strictly-larger released slot WHOLE. No rewind of the region offset; the monotonic-region invariant survives. Its own boot proof (`reuse_self_test`, run immediately before the terminal guard demo) allocates, releases, re-hands-out, verifies the reused slot THROUGH THE PAGE TABLES (verify(): guard unmapped, pages present), and proves the absorption bounds (whole slot, zero splits). Hand-back sites: originally hypothetical, now REAL at the one place a space is actually torn down — see the LIVE-slot hand-back bullet below. What remains not claimed: a live IN-USE stack released while someone is running on it; no such site exists and none is needed until tasks can exit mid-chain.
- **The lazy-binder's EMPTY-registry refusal branch, proven kernel-side** (close-out of the typed-refusal round, after its `UnregisteredTrampoline` control): that round's `LazyBindRefusal` enum and `probe_unregistered` left the `ServiceEmpty` variant not yet boot-fact'd — only the registered-side refusal was. Now the full demo — the site of the CHAIN's first `lazy_binder::register` call — probes the service immediately BEFORE that registration, at the one point of every boot where the registry is genuinely empty, and pins the typed refusal EXACTLY as `ServiceEmpty` (not the `UnregisteredTrampoline` the same shared `identify` path serves once entries exist; the deps demo's negative control, against the same probe, runs later with count 1, so the two variants are distinguished by the same probe in the SAME boot). The outcome is an assert at the registration site (the proof fails the boot, not just the transcript) plus a transcript fact line. Design note: `probe_unregistered` cannot be run against the 0x88 trap path for this branch without killing the boot — the handler still panics loudly on any refusal (arm-before-use remains the wiring contract) — so the probe, not the trap, IS the proof surface, same as that round's control.
- **The exact-count reuse pool's page-count matching, proven BOTH directions** (close-out of the slot-reuse round): that round's `reuse_self_test` proved a released 2-page slot satisfies a 2-page request and that a different count (3) bumps fresh; this increment added the REVERSED direction — a released 3-page slot must not silently satisfy a later 2-page request — asserted two ways (the reversed request checked against `NEXT_OFFSET` exactly, and its slot base differing from the released 3-page slot's) and folded into the same transcript fact line. **Update (current increment): this rule is superseded by WHOLE-SLOT ABSORPTION** — a smaller request now takes the smallest strictly-larger released slot, but ENTIRELY and only then: exact matches still win, the absorbed descriptor keeps the slot's own page count (never resized to the request), and splitting is impossible by design, not just unimplemented — each slot's guard is the ONE unmapped page separating it from its neighbor, so a split-off remainder would inherit a MAPPED page as its "guard" and the exact guard invariant every proof re-verifies through the page tables could not survive. The absorption counter's bounds are boot-asserted (region offset frozen across the absorb, descriptor stays 8 pages, same guard/top, verified afresh), and a BIGGER request still never touches a smaller pooled slot (fresh bump).
- **The layout gate's coverage rule**: the dependency-CYCLE and DEPTH-CAP negative controls run their deliberately-broken catalogs over exactly the artifact set the gate fact-checks, and the gate states that rule — a future provider cannot enter the demo tree (as a real edge or as a control) without its own factcheck block appearing first.
- **The FIRST LIVE on-DRAM-slot hand-back, proven at the one real teardown site** (`memory::kernel_stack::release` ← `AddressSpace::destroy` ← `ring3_scheduler::finale`): until now every claim of slot reuse was self-test-only — no running demo ever released anything. This closes that: `destroy` no longer hand-rolls its stack reclamation (the same unmap + flush + frame-free it used to keep in parallel with `release`, a drift hazard), it calls `kernel_stack::release`, so EVERY destroyed address space's RSP0 slot flows into the exact-count pool — and the boot already has a real teardown site: the ring-3 scheduler finale destroys suspended task F, whose space was built through `new_sharing_kernel` so its slot IS a pool member. The proof pins the semantics concretely on that: F's stack descriptor is captured before destroy, the descriptor demands the default budget, and after the preemption-proof tasks are built the finale asserts task H's live RSP0 stack base/guard/top equals F's released slot's — byte-identically — and re-verifies it through H's OWN page tables (guard unmapped, pages present); task I (built next, pool consumed) must be a fresh bump. Nothing about the proof is inferred from code reading: the strongest assert fires if a live task ends up on a wrong-sized or half-reclaimed slot. (Nothing about `teardown-demo`'s earlier X/Y destroys breaks the proof: X's released slot is immediately re-consumed by that demo's own space Y, so by the time finale runs the pool holds exactly F's slot — and the asserts, not the prose, are what's verified.) Scope note: still no live IN-USE stack released mid-execution; the (rare) teardown sites outside `destroy` — the `mem::forget`ed demo spaces — remain deliberately leaked.
- **The negative-control windows are now a CI-gate rule, not just prose** (`tools/verify_prog_layout.sh` scratch-window pin): the kernel's negative controls load deliberately-refused objects and probe deliberately-unregistered trampolines at KERNEL-PICKED bases (deps demo's cycle scratch 0x6D0_0050_0000 / depth-cap 0x6D0_0055_0000 / lazy-binder probe page 0x6D0_0057_0000; full demo's refusal carriers SCRATCH_BASE_A/B). A new gate section parses every embedded artifact's PT_LOAD range out of real readelf output and REFUSES THE BUILD if any artifact is ever pre-linked (non-PIE, fixed-placed) or drifts into a window — so a control can never land mid-artifact or probe inside a real mapping and mean something other than what the demo asserts. Today every artifact is ET_DYN with PT_LOAD VirtAddr near 0 (the demos choose bases at runtime) so the check passes trivially — exactly the property a gate pins before it becomes a regression — and the overlap arithmetic itself was probe-verified to concede: a synthetic artifact placed at the probe page is refused, the current PIE artifacts pass. Real bugs already caught by rounds of this block (1) the demo hash-tag constant was wrong (digit-swap, above), so the walk never saw real GNU data from the demo-direction; (2) the GNU walker originally REUSED the SysV ELF hash function (above); (3) `build.sh`'s echo line around `.weak` — the backquotes were being expanded by bash as command substitution, so `.weak` was run as a COMMAND on every build (harmless-looking failure, actually eats the script confirming that the weak-variant half of the gate had ALREADY passed); (4) the flag patcher's readelf regex had to cross the line between the section title and its `Addr:` header, and missed it without `re.DOTALL` (composed with the same bug-fix discipline: verified against the REAL readelf layout); (5) the deps-demo stage overflowed the shared 5-page default RSP0 budget on the new depth-2 work — a #PF at the guard escalating to #DF, zero corruption, exactly what the guard exists for — and the per-stage declaration above is the fix at the RULE level (the shared default stays; the demanding stage declares its own budget).

**2026-10-14 (seventh round) — recursive DT_NEEDED through a catalog, interposition beyond load order, weak versioned symbols, and the lazy binder as a real shared SERVICE:**

- **Recursive dependency loading from a named catalog** (`elf::DepCatalog` + `elf::load_tree`): the "`DT_NEEDED`-driven dependency discovery needs a filesystem" limitation is now OPEN-CLOSED by rule, not hardware: a named, in-memory table (SONAME -> embedded file bytes + load base — exactly what `/lib` + a search path becomes when Phase 8's storage arrives) lets the loader automatically satisfy a dependent's declared dependencies DEPENDENCY-FIRST, recursively, before relocating the object that needs them. Cycle handling is typed (`DependencyCycle`: a file can never load itself to satisfy itself — proved every boot against a deliberately looping catalog) and so is the depth cap (`DependencyDepthExceeded`). The verified chain: `userprog_deps.elf` NEEDs `crosslib2.so.1`, which NEEDs `crosslib.so.1` — one call loads both providers before the dependent relocates (the catalog auto-loaded object count is a checked fact, not prose). What this does NOT pretend: a filesystem. The catalog cannot find anything not registered into it, the loader probes no storage.
- **Interposition beyond load order** (`SymbolRegistry::interpose`): an explicit overlay that is consulted BEFORE the load order for every object loaded LATER (the same one-way effect `LD_PRELOAD` has; already-relocated slots keep their resolutions). Proven every boot with a REAL name conflict across providers: the overlay's `interp_mark` (unversioned, deliberately absent from the earlier provider) resolves to the OVERLAY's address through the dependent's GLOB_DAT slot, and the ring-3 payload reads the overlay's immediate (0x1111222233334444) through it. A pre/post `lookup` pair pins the flip.
- **Weak versioned semantics** (`STB_WEAK` binds + `VER_FLG_WEAK` verneed flags): a WEAK undefined symbol — or a VERNEED requirement carrying `VER_FLG_WEAK` — resolves to the NULL slot (0) when no candidate exists and the load PROCEEDS (the same posture glibc takes; the program owns the consequence of dereferencing 0). A real artifact exercises it: `prog_deps_dep.s` carries a compiled `.weak ghost_fn` UNDEF GOTPCREL reference with NO provider defining it — the dependent's ghost GLOB_DAT slot reads NULL-slot 0, its GNU_RELRO region still revokes read-only (weak != denial), and the loader did NOT refuse. Strong requirements stay hard, typed refusals (unchanged; the sixth round's `VersionNotProvided` negative control still fires every boot). Honest note: the PLANTED proof runs through the SYMBOL-side weak bit; the VERNEED-flag half is parsed and honored (all three passes — relocation, RELRO planning, lazy trap — share the same tolerance rule) but no natural toolchain output in this repo carries a flag-weak VERNEED, because GNU ld only emits `VER_FLG_WEAK` on an aux entry when ALL its binds are weak, and this dependent's strong `cross_value@VERS_1.0` shares the file entry. **Update 2026-10-15 (eighth round): the FLAG half is now proven on a REAL (`readelf`-verified) artifact too — a dedicated weak-only dependent whose flag is composed deliberately into the file by a checked build step with a gate pinning `Flags: WEAK`; see the eighth-round block above.** The original statement (no natural toolchain emission in this environment) stands, verified empirically.
- **The lazy binder is a SERVICE, not a demo** (`experimental::lazy_binder`): the `0x88` handler now dispatches through a registry of ALL lazy-armed objects — identified by their own trampoline page — so the FULL demo's dependent AND the deps demo's dependent (loaded later, different address space, different resolver scope) both bind through ONE vector in ONE boot: 2 independent objects registered, 2 binds served. Per-object bind counts keep each laziness claim checkable (exactly 1 trap across that object's repeated calls); the service totals keep the single-vector fact checkable. The full demo migrated off its demo-private `on_plt_trap` statics onto the service (a rule cleanup, not a behavior change — its transcript lines are byte-identical except naming the service). 
- **Deeper chain, deeper stacks** (`memory::kernel_stack::DEFAULT_STACK_PAGES` 5 -> 8): the demo chain's completion traps never return, so each chained demo's frames ACCUMULATE on the current kernel stack across the whole boot; the seventh demo's recursive loader path pushed the accumulated depth past 5 pages, and the architecture's guard page faulted LOUDLY (verified: a #PF at the guard address escalating to #DF, zero corruption) rather than silently overwriting the heap — the system did exactly what the guard exists to do. The default now reflects the measured chain depth, with the reason written next to the constant.
- **The CI layout gate** (`tools/verify_prog_layout.sh`, hooked into `build.sh` BEFORE kernel code ever sees the .so files): every embedded object's load-bearing facts are readelf-checked — SONAMEs, VERDEF/VERNEED presence with the expected version names, the DT_NEEDED chain edges, the JUMP_SLOT/JUMP_SLO (readelf column truncation: encoded in the gate), GLOB_DAT surface, and the WEAK UNDEF ghost entry. A drift refuses the build with the exact absent pattern and the full readelf surface, so file-fact drift is a build error instead of a boot-time panic hunt. First integration already paid for itself: it mis-flagged `JUMP_SLOT` vs readelf's truncated `JUMP_SLO` output, which is now an explicit pinned fact.
- **Slot facts by SYMBOL, not only by pinned offset** (`elf::find_slot_for_symbol`): the new demo checks locate GLOB_DAT/JUMP_SLOT slots by the SYMBOL they bind (through the version-aware tables), so the checks stay correct when the layout tool changes section placement — the static readelf pins remain as the CI gate's file facts.
- Real bugs caught on the first boots, fixed at cause: (1) the link-time removal of an unreferenced weak symbol left the `.symver`-decorated variant un-linkable (`no symbol version section for versioned symbol`) — the reference had to be REAL (the payload now stores the ghost slot ADDRESS, never dereferencing the null) and plain-reference auto-versioning does the version attribution the way real linkers do; (2) the interposated observable was flagged UNDEF with a VERSION (the linker auto-bound it to a versioned provider's definition), which would have eaten the file-binding requirement the overlay cannot satisfy — renamed to a deliberately unversioned symbol so the interposition question and the version question stay independent (both explained in the source files' headers); (3) the versioned GLOB_DAT slot check first compared slot CONTENT SYMBOL ADDRESS against the DATA value expected — the demo corrected to compare address-vs-resolution and let the payload's deref prove the data, exactly the same 32/64-bit ABI-mismatch correction pattern as the sixth round, caught by the boot transcript before the assert.
- Verified transcript lines (from `tools/qemu_boot_test.sh`, passing twice consecutively; 181 OK lines total, all prior demos still present):

  ```
  experimental deps-demo: provider A loaded (crosslib.so.1) -- OK
  experimental deps-demo: pre-overlay lookup of interp_mark = absent (provider A does not define it) -- OK, absent before the overlay exists
  experimental deps-demo: after interpose(overlay), lookup of interp_mark = 0x6d000103000 (data 0x1111222233334444, the overlay's value) -- OK, interposition overrides load order
  experimental deps-demo: looping catalog refused with DependencyCycle -- OK, a file can never load itself to satisfy itself
  experimental deps-demo: DT_NEEDED CHAIN (dependent -> crosslib2.so.1 -> crosslib.so.1) satisfied dependency-first; 1 catalog object(s) auto-loaded -- OK
  experimental deps-demo: dependent's GNU_RELRO revokes read-only WITH an unresolved WEAK entry in-region (weak != denial) -- OK
  experimental deps-demo: dependent's versioned GLOB_DAT slot 0x6d000302fe0 == 0x6d000003000 (provider's cross_value as resolved BY THE CATALOG CHAIN) -- OK
  experimental deps-demo: dependent's interp_mark slot 0x6d000302fd8 == 0x6d000103000 (the INTERPOSITION overlay's address) -- OK
  experimental deps-demo: WEAK UNDEF ghost_fn@VERS_1.0 slot 0x6d000302fd0 == 0x0 (the NULL slot; unresolved weak stays non-fatal) -- OK
  experimental deps-demo: lazy JUMP_SLOT slot 0x6d000303000 == 0x6d000310000 (trap stub armed on THIS object's own trampoline page) -- OK
  experimental deps-demo self-test: cross_value = 0xace5f00dd1ce5eed (expected) -- OK
  experimental deps-demo self-test: interp_mark = 0x1111222233334444 (the overlay's value read through ring 3) -- OK
  experimental deps-demo: lazy-bind service fired 1 time(s) across 3 calls (exactly 1 required) -- OK
  experimental deps-demo: shared lazy-bind service: 2 independent object(s) registered, 2 bind(s) served through ONE vector (0x88) this boot -- OK, the vector is a SERVICE, not a demo
  ```

  Warning accounting: +7 warnings, ALL the pre-existing `reference to a mutable static` category (the new demos' armed-state statics, the same shape every prior demo carries); no new categories and no `#[allow]`.

**2026-10-14 (sixth round) — the full dependent contract: DT_NEEDED, versioned symbols, and lazy PLT binding — the three limitations the fifth round listed as open, closed together:**

- **The `DT_NEEDED`/`DT_SONAME` dependency contract** (`experimental::elf::load`): the loader parses `DT_NEEDED` and consults the resolver's new `has_soname` for each declared dependency BEFORE any relocation work; a contract the declared scope cannot satisfy refuses the whole load with the typed `MissingDependency` (a distinct error from `UndefinedSymbol`, because "you did not provide the object I named" is not "that symbol does not exist"). A resolver that does not model dependency contracts answers `true` by default — explicit, so a silent no-veto cannot masquerade as enforcement. Proven every boot against an empty registry (refused), and against the correctly-registered provider (load proceeds).
- **Versioned symbol resolution** (`experimental::elf`): real `DT_VERSYM`/`DT_VERDEF`/`DT_VERNEED` parsing (aux/parent vends walked, `vd_flags`/`vd_ndx` honored by index), and the `SymbolResolver` trait's new `resolve_versioned(name, (version, soname))` form, whose [`SymbolRegistry`] implementation requires a candidate to satisfy BOTH the required version AND the required file/SONAME before a match counts. A same-SONAME provider declaring only `VERS_2.0` refuses the `@VERS_1.0` requirement with the typed `VersionNotProvided` — the name alone is provably NOT enough, checked every boot. A symbol with a required version and NO version-aware resolver stays refused: unversioned resolvers are explicitly incompatible, never silently version-blind.
- **Lazy PLT binding** (`experimental::elf` + a new IDT service): `DT_JMPREL`/`DT_PLTREL`/`DT_PLTRELSZ` are parsed with the other dynamic tables, but `R_X86_64_JUMP_SLOT` entries bound lazily no longer resolve at load. `load` instead takes optional `LoadContext { resolver, lazy_plt_page }` and on commitment emits a 16-byte trampoline per slot into a loader-mapped, kernel-executable page — `push idx; int 0x88; add rsp, 8; jmp *[%rip-rel32]` pointing at the GOT slot — then publishes the stub's address in the slot. The first user call executes the stub and traps; the (kernel, ring-0) `0x88` handler reads the pushed index off the user stack and calls the new `elf::resolve_lazy_entry`, which applies the SAME self-dynsym-then-registry, version-and-SONAME-checked rules as the load-time pass, patches the actual slot address via the new `elf::lazy_slot_for`, and returns so the stub's tail jumps through the now-real target. Every later call takes the direct path. Proven every boot: exactly ONE trap across THREE calls of the dependent's function, and the first (lazy-bound) and second (direct) calls return identical results.
- **The demo** (`experimental::elf_dyn_full_demo`, chained after `elf_dyn_cross_demo` on the ring-3 chain): one dependent carries the whole contract AT ONCE — `DT_NEEDED crosslib.so.1`, a `@VERS_1.0`-required `cross_value` GLOB_DAT, a lazy `cross_fn` JUMP_SLOT/PLT call, and its own `dup_symbol` (the self-priority control re-proven under versioned lookup). The `V1`/`V2` provider pair is the same source linked twice by `build.sh` with `-soname crosslib.so.1` plus two `--version-script` files (`userprog/ver_lib_v1.ld`/`ver_lib_v2.ld`), so nothing is hand-simulated; the dependent is a real `ld -shared dep.o provider.so` link, which is where its `DT_NEEDED`/VERNEED come from. `readelf`-verified the whole layout (SONAME present, VERDEF nodes, VERNEED's `vn_other` file-binding linkage to the CORRECT provider, JMPREL surface) before any kernel code touched the files. IDT vectors: `0x88` (the lazy binder, a shared service — multiple dependents could register against it) and `0x89` (the demo's completion/chain trap).
- **Toolchain/ABI facts the demo honors**: `cross_fn` returns its result in `%eax` only (a 32-bit store); the completion check compares the 32-bit return width the ABI actually delivers, not a full-rax read (whose upper bits are caller-leftover and no callee's responsibility) — that exact mismatch was one of two real bugs this increment caught, see below.
- **Real bugs caught on the first boots, fixed at cause (the pattern holds):** (1) the lazy-stub emitters encoded `rel32 = slot - (stub + 14)` as a plain u64 subtraction, which panics in debug builds (`attempt to subtract with overflow`, the boot's panic named the exact line) whenever the trampoline page's frame lands BELOW the GOT slot — legal, since the address is allocator-chosen; fixed with `wrapping_sub`, and the comment records why the wrap is the CORRECT encoding, not a workaround. (2) with lazy binding on, every boot PANICKED at the completion check's `fn_ok` because the check read 8 bytes where the payload stored 4 (`mov %eax`) — the mismatch in the serial transcript was REAL, not a test artifact, so the check was corrected to the declared 32-bit contract (see the fact above). Both failures surfaced as boot-test timeouts that the fault reports narrowed in one step.
- Verified transcript lines (from `tools/qemu_boot_test.sh`, passing twice consecutively; all prior demos' lines still present):

  ```
  experimental full-demo: provider loaded; DT_SONAME fact (crosslib.so.1) validated -- OK
  experimental full-demo: same-SONAME V2 provider refuses the VERS_1.0 requirement with VersionNotProvided -- OK, version matching is enforced; the name alone is NOT enough
  experimental full-demo: dependent's versioned GLOB_DAT slot 0x6c000202fe0 == 0x6c000003000 (provider's cross_value, version-checked) -- OK
  experimental full-demo: dependent's duplicate-name slot 0x6c000202fd8 == 0x6c000203008 (its OWN dup_symbol) -- OK, self-priority held under the versioned provider too
  experimental full-demo: lazy JUMP_SLOT slot 0x6c000203000 == 0x6c000210000 (trap stub armed; NOT eagerly resolved) -- OK
  experimental full-demo: empty-registry dependent refused with MissingDependency -- OK, the declared dependency contract is enforced
  experimental full-demo: V2-registry dependent refused (denied region or typed version error) -- OK, the version contract refuses end to end
  experimental full-demo self-test: cross_value via versioned slot = 0xace5f00dd1ce5eed (expected 0xace5f00dd1ce5eed) -- OK
  experimental full-demo self-test: cross_fn results (eax) 0xfa571ae5 / 0xfa571ae5 (expected 0xfa571ae5 both; first via lazy bind, later direct) -- OK
  experimental full-demo self-test: lazy-bind trap fired 1 time(s) across 3 calls (exactly 1 required) -- OK, binding is lazy; later calls are direct
  ```

  Honest scope, unchanged in kind: the contract is between ALREADY-LOADED objects — the decorated scope, not the filesystem. Finding and loading a `DT_NEEDED` file from storage remains Phase 8 scope (needs a filesystem surface); lazy binding resolves through the same explicit registry and version contract as the eager pass, not a GNU-style .plt resolver with its own hash tables; and `LD_PRELOAD`-style interposition beyond the documented load-order search is still not modeled. Unresolvable requirements are refused with TYPED errors (`MissingDependency`/`VersionNotProvided`/`UndefinedSymbol`), never mis-resolved. See the "Real dynamic linking" entry below for the current remainder.
  Warning accounting: the increment removes the one warning it
  initially introduced (an unused `mut` on the negative-control probe
  variable) and adds no suppressions: the two `reference to a mutable
  static` instances in `elf_dyn_full_demo` are the same pre-existing
  category every demo's static hand-off of the frame allocator to a
  ring-0 trap handler carries. No `#[allow]` was added anywhere.

**2026-10-13 (fifth round) — cross-object symbol resolution: ANOTHER
real `ld -shared` .so now loads whose relocations genuinely need the
registry, resolving an UNDEFINED symbol against an earlier-loaded
object:**

- **A registry-based `SymbolResolver`** (`experimental::elf`): the
  loader's relocation pass now consults an optional resolver for
  `SHN_UNDEF` symbols (`R_X86_64_GLOB_DAT`/`JUMP_SLOT`/`64` entries
  whose symbol is not defined in the object itself). What the resolver
  cannot resolve stays REFUSED — `UndefinedSymbol` — the same
  never-guess posture, now enforced THROUGH the resolution path
  (every-boot check: the identical dependent object loaded WITHOUT a
  resolver comes back refused — `Ok(relro = Denied)`, the loader's
  documented honest state — while the same bytes load and run WITH
  the registry).
- **The search discipline, documented and pinned every boot**:
  the object being relocated resolves its OWN dynsym entries first
  (its defined symbols are taken from the FILE, never displaced by a
  registered object's same-named definition), then registered
  objects resolve in load order, first DEFINED match wins. Two real
  .so pair exposes both rules observably: the dependent re-exports
  `dup_symbol` which the provider ALSO exports (distinct immediates)
  — the dependent's GOT slot must hold ITS OWN definition while the
  provider's table still resolves to the provider's, and two
  registries built in opposite orders return observably different
  addresses. A lazy by-name registry pass would MISMATCH loudly on
  the duplicate-name slot.
- **The dependent object itself** (`userprog/prog_cross_app.s`,
  `ld -shared`): `cross_value` referenced `@GOTPCREL` with NO local
  definition — `ld -shared` materializes it as a real
  `R_X86_64_GLOB_DAT` against a `SHN_UNDEF` dynsym entry, the exact
  file shape that needed the refusal type before a resolver existed.
  Both of its GOT slots sit inside its GNU_RELRO region
  (0x2ee8..0x3000), so the RELRO planner had to consult the resolver
  for the UNDEF-but-resolvable entry: the grant/revoke cycle ran and
  landed read-only. The provider (`userprog_cross_lib.elf`) is a
  data-export-only .so — registered into the registry after its own
  load completes, structurally guaranteeing self-priority.
- **The behavioral half**: the dependent's own `cross_payload` code
  runs in ring 3, dereferences its registry-resolved GOT slot, and
  reads the PROVIDER's data — the value only the cross-object
  resolution path could have produced (its local lookup refuses;
  the transcript shows BOTH facts).
  Verified transcript lines (from `tools/qemu_boot_test.sh`,
  passing; two consecutive runs, 155 OK lines each — all prior
  demos' lines still present):

  ```
  experimental cross-demo: provider registered (registry 1 object(s)); cross_value 0x6c000002000, dup_symbol 0x6c000002008 resolve from the provider's own tables -- OK
  experimental cross-demo: dependent's GNU_RELRO region 0x6c000042ee8..0x6c000043000 revoked read-only; the grant half ran for both in-region GLOB_DAT slots (one UNDEF-but-resolved) -- OK
  experimental cross-demo: the app's own by-name lookup of cross_value refused with UndefinedSymbol -- OK, the app genuinely does not define it
  experimental cross-demo: app's UNDEF GLOB_DAT slot 0x6c000042fe0 == 0x6c000002000 (the PROVIDER's cross_value, via the registry) -- OK, cross-object relocation applied correctly
  experimental cross-demo: app's duplicate-name GLOB_DAT slot 0x6c000042fd8 == 0x6c000043000 (ITS OWN dup_symbol) -- OK, self-priority: the app's OWN dynsym entry won, not the registry's earlier same-named entry
  experimental cross-demo: registry search order decides -- [app,lib] -> 0x6c000043000 (app), [lib] -> 0x6c000002008 (lib) -- OK, first DEFINED match in order wins
  experimental cross-demo: absent-name registry lookup returned Ok(None) (typed absent, no parse error) -- OK
  experimental cross-demo: the same dependent WITHOUT a resolver refused with Ok(relro = Denied) -- the caller must refuse -- OK, the refusal is NOT gone now that resolution exists
  experimental cross-demo self-test: the dependent's own code read 0xace5f00dd1ce5eed through its cross-resolved GOT slot (expected 0xace5f00dd1ce5eed, the provider's cross_value) -- OK
  ```

  A real bug caught on the first boot, fixed at cause (the pattern
  holds): the dependent's private-page immediate in the assembly was
  one placement off from the demo's constant (`0x6c000800000` vs
  `0x6c000080000`), so the ring-3 store missed its mapped page and
  the boot timed out — with the fault report naming the exact
  write-address and the exact instruction, narrowing it to a
  single-digit layout mismatch. The loader's sanity was confirmed by
  what did NOT happen: both GOT reads and dereferences succeeded.

  Honest scope, unchanged in kind: this is a load-order registry
  with one documented rule — not `ld.so`-grade dependency-graph
  walking. `DT_NEEDED` (dependency discovery from filenames, needs a
  filesystem surface), interposition beyond registry order, and lazy
  PLT binding (JUMP_SLOT entries bind eagerly at load, equivalent to
  `-z now`; a lazy .plt-resolver trampoline stays future work) are
  the open track. See the "Real dynamic linking" entry below.

**2026-10-13 (fourth round) — in-object symbol resolution: a real
`ld -shared` shared object now loads, relocates, resolves symbols by
name, and its own code runs in ring 3:**

- **Symbol-resolved relocations** (`experimental::elf::load`): the
  loader now applies the in-object symbol-resolved relocation class —
  `R_X86_64_GLOB_DAT`/`R_X86_64_JUMP_SLOT` (`S`) and `R_X86_64_64`
  (`S + A`) — where `S` is `load_bias + st_value` of a symbol DEFINED
  in the object. A symbol-resolved entry against a genuinely UNDEFINED
  symbol (`SHN_UNDEF`) is refused with the typed `UndefinedSymbol` —
  cross-object resolution is real dynamic linking's work and remains
  the documented boundary (see the "Real dynamic linking" entry
  below).
- **The SysV dynamic tables**, parsed once from the mapped
  `PT_DYNAMIC` with the same paranoia the format has met all along:
  `DT_HASH` nchain capped at a sane 2^20, `DT_SYMENT` must be 24,
  string offsets bounded by `DT_STRSZ`, names NUL-terminated inside
  the string table, malformed dynamic surface = typed
  `MalformedDynamicTables`, never a guess.
- **By-name symbol lookup** (`elf::lookup_symbol`): a real SysV
  DT_HASH bucket/chain walk (the mechanism a dynamic linker uses for
  this table kind, not a linear scan), with `SymbolNotFound` typed
  DISTINCT from parse errors so an absent name is a checkable outcome
  — the demo's negative control resolves a deliberately absent name
  every boot.
- **The RELRO grant half ran for the first time.** `ld -shared` places
  `.got` INSIDE the `PT_GNU_RELRO` region (0x2ef0..0x3000 in this
  object) and the in-region GLOB_DAT needs the write-grant, so the
  grant/revoke cycle finally exercised both halves. That exposed a
  real correctness bug in the flag-update pass, caught during this
  increment's review and fixed before the first boot: `Mapper::update_flags`
  REPLACES the whole flag set, and the pass had been handing it a
  synthetic (`PRESENT`-based) result — silently stripping
  `USER_ACCESSIBLE` and `NO_EXECUTE` from the RELRO pages, i.e.
  exactly the pages the loaded code's GOT reads touch (ring 3 would
  have faulted reading its own resolved GOT slot). The grant/revoke
  now read each page's CURRENT leaf PTE flags first (through
  `memory::paging::page_flags_of`) and change exactly one bit. The
  every-boot check now also asserts the RELRO page is still
  USER_ACCESSIBLE after the revoke — the property whose silent loss
  the bug was.
- **Toolchain verified first**, same discipline as PIE/PE: `readelf`
  on the hand-built object (`userprog/prog_dyn.s`, linked plain
  `ld -shared`) confirmed DT_HASH present (not just DT_GNU_HASH),
  DT_SYMTAB/DT_STRTAB, exactly two symbol-resolved relocations
  (`R_X86_64_GLOB_DAT` r_offset 0x2fe0, in-`.got`; `R_X86_64_64`
  r_offset 0x3008, in-`.data`), GNU_RELRO 0x2ef0..0x3000, and 5
  dynsym entries — before any kernel code touched the file.
- **An assembly-level fact worth recording:** a plain RIP-relative
  read of an EXPORTED symbol does not link into a shared object
  (`ld -shared` refuses the `R_X86_64_PC32`; exported symbols are
  preemptible). The loaded code instead dereferences `dyn_value`
  through the linker-created GOT (`mov dyn_value@GOTPCREL(%rip)`) —
  exactly the producer convention that forces the `R_X86_64_GLOB_DAT`
  entry the loader processes. The demo's behavioral half therefore
  exercises the real GOT mechanism, not a special case.
- **Chain wiring**: `pie_demo`'s trap now hands control to
  `elf_dyn_demo` (its own vector, 0x86), whose `on_trap` chains into
  `elf_dyn_cross_demo` (0x87) and thence into `pe_demo` — the same
  one-way demo-chain structure as every other ring-3 demo.
  `kernel_main`'s direct flow is unchanged (so
  isolated-tasks, DEX, teardown, and the ring3-scheduler
  phases all still run before the chain's final leg). The program's
  trap-handler check is the behavioral proof: the `.so`'s own code,
  running in ring 3, reads its resolved GOT slot and produces the
  value only a correctly resolved/relocated load could.
  Verified transcript lines (from `tools/qemu_boot_test.sh`, passing;
  all prior demos' lines still present):

  ```
  experimental elf-dyn-demo: GNU_RELRO region 0x64000002ef0..0x64000003000 revoked read-only after grant/relocate (the GRANT half ran for the first time) -- OK
  experimental elf-dyn-demo: resolved by name via the DT_HASH walk -- dyn_payload 0x64000001000, dyn_value 0x64000003000, dyn_timestamp 0x64000003010 -- OK
  experimental elf-dyn-demo: absent-name lookup returned the typed SymbolNotFound (not a parse error) -- OK
  experimental elf-dyn-demo: GLOB_DAT slot 0x64000002fe0 (in-RELRO .got) == 0x64000003000 (resolved dyn_value) -- OK, symbol-resolved relocation applied correctly
  experimental elf-dyn-demo: R_X86_64_64 slot 0x64000003008 (writable .data) == 0x64000003000 (resolved dyn_value) -- OK, symbol-resolved relocation applied correctly
  experimental elf-dyn-demo: RELRO page 0x64000002000 is read-only and user-readable after the grant/revoke cycle -- OK
  experimental elf-dyn-demo: GLOB_DAT slot residency check: 0x64000002fe0 inside RELRO region 0x64000002ef0..0x64000003000 = true -- OK
  experimental elf-dyn-demo: wrote 0x5eed0001 through resolved dyn_timestamp (0x64000003010) and read it back -- OK, a resolved runtime address is a real mapped address
  experimental elf-dyn-demo: kernel-side proof complete; entering dyn_payload (ring 3) which dereferences its own resolved GOT slot
  experimental elf-dyn-demo self-test: the .so's own code read 0xd1ce00f00d through its resolved GOT slot (expected 0xd1ce00f00d) -- OK
  ```

  Honest scope, unchanged in kind: WITHIN-object resolution only. A
  genuinely undefined symbol is refused (`UndefinedSymbol`), not
  mis-resolved; dependency walking, symbol interposition, and lazy PLT
  binding remain the open cross-object track (see the "Real dynamic
  linking" entry below).

**2026-10-12 (third round) — the GNU_RELRO "make read-only again after
relocating" gap closed, with an every-boot structural proof:****

- **PT_GNU_RELRO enforcement** (`experimental::elf::load`): the loader
  now parses the `PT_GNU_RELRO` program header and runs the real
  three-step discipline — map each segment exactly as the file's
  `p_flags` request (no blanket-writable compromise), grant
  `WRITABLE` on the region's pages *only when* relocation targets
  actually land inside it (a pre-walk of the relocation table without
  applying anything), apply the relocations, then UNCONDITIONALLY
  revoke `WRITABLE` on the success path — restoring precisely the
  file-requested permission set, never widening anything the file did
  not grant (an executable relro region cannot come out writable, and
  a non-executable one cannot come out executable). The revoke is
  unconditional for a reason the discipline itself makes easy to get
  wrong: a GNU_RELRO region often overlaps a writable `PT_LOAD`
  segment (here the relro covers `PT_DYNAMIC`, inside the RW data
  segment), so "no in-region relocations" does NOT imply "already
  read-only" — the first boot with the structural check caught
  exactly that variant (the revoke was initially skipped on the
  no-in-region-relocation path, the real RELRO page read back
  `writable=true`, the boot refused to pass). Two honest bugs in this
  round's own checks were caught and fixed at cause rather than
  softened: that skipped-revoke-on-no-relocation-path logic bug, and
  a decorative-check bug in the demo itself (the structural check
  first examined a page that would be read-only regardless of the
  RELRO pass — the ELF header page — so it could not fail if the
  revoke pass were removed; it now walks the loader's own reported
  `RelroOutcome::MadeReadOnly` range, stashed at load time, and
  asserts `checked > 0` — which is also how the bug above was caught,
  as the boot residue of the second check-rewrite). If the relocation
  pass is refused (an unsupported, symbol-resolved type), the region
  is left writable and reported via `LoadedElf::relro = Denied(start,
  end)` — the honest state surfaced to the caller rather than
  silently kept read-protected or silently left writable; `pie_demo`
  refuses such a binary outright, so "denied" cannot ride a silent
  path through the boot. The kernel also gained
  `memory::paging::page_flags_of`, which reads a page's leaf PTE
  directly through CR3's tables (some checks need to *observe*
  permission bits, not infer them from later fault behavior — the
  same lesson `guard_demo`'s probe taught in the opposite direction),
  and `pie_demo` uses it every boot to verify every GNU_RELRO page of
  the loaded PIE — the loader's own reported range, not a hand-copied
  constant — is genuinely `writable=false` in the live page tables
  *after* relocation, independent of the loader's internal state, so
  a loader regression cannot coincide with a self-test regression in
  the same increment.
  Verified transcript lines (same passing boot as the entry above):

  ```
    experimental pie-demo: GNU_RELRO region 0x60000002ef0..0x60000003000 is back to read-only after relocation (loader grant/revoke cycle completed) -- OK
    experimental pie-demo relro check: page 0x60000000000 PTE flags writable=false -- OK
    experimental pie-demo self-test: all 1 GNU_RELRO page(s) verified read-only in the live page tables after relocation -- OK
  PASS: kernel reached BootPhase::Ready and exited cleanly
  ```

  Honest scope note, unchanged in kind: this covers RELATIVE-relocatable
  binaries only (the same scope the relocation support itself has).
  Symbol-resolved GNU_RELRO (dynamic linker scenarios) is captured by
  the "Real dynamic linking" entry below — the RELRO pass denies and
  reports those rather than pretending.

**2026-10-11 (second policy round) — three more documented gaps closed,
each with an every-boot self-test:**

- **RTC wall-clock time** (the "does NOT exist yet" entry that said
  reading the CMOS time registers was "deliberately left out"): `rtc::read_time`
  reads seconds/minutes/hours, decoding per Status B's ACTUAL mode bits
  (binary vs BCD; 12-vs-24-hour with PM normalized), with a bounded
  update-in-progress wait per register (never hangs; a stuck UI flag is
  a typed failure). `self_test_time_registers` decodes real values per
  boot (verified transcript: `08:57:10` decoded via BCD/24h), asserts
  field ranges through whichever mode the firmware reports, and proves
  read-order monotonicity plus tick correlation between reads.
- **Task termination states** (NPS-002 §5.5/§5.6), after their legal
  edges landed in the first round's lifecycle: `terminate_current()`
  walks RUNNING → TERMINATING → TERMINATED through the enforcer and
  dispatches away; task J exists solely to terminate on its first
  activation, and the exact-count assertions prove a terminated task is
  never re-dispatched (J runs exactly once; A/B still reach exactly 6
  each across a 4-task round-robin). `self_test_terminated_consistency`
  checks TERMINATED is unreachable directly from CREATED/READY and the
  remaining tasks are all active.
- **Blocking receive** (NPS-003 §3's `receive` primitive):
  `ipc::receive(dest, max_polls)` yields the core between polls (an
  idle receiver never spins hot) and returns the typed
  `ReceiveTimedOut` when its budget elapses — "a defined error rather
  than blocking indefinitely". Verified both ways every boot: an empty
  mailbox times out typed, then a send makes the same receive deliver
  exactly that message.

Two honest self-test bugs this round's own boot caught and fixed (the
pattern holds): the termination consistency check initially asserted
its own stale precondition ("no task has terminated" — false after task
J legitimately terminates, caught on the first boot); and the blocking-
receive positive half reused the revocation test's scratch mailbox,
whose sender-0 revocation made the honest send correctly return
`Err(Revoked)` (also caught on the first boot). Both fixed at the
cause; the negative-path work the checks do is unchanged.

Verified transcript lines (from `tools/qemu_boot_test.sh`, passing):

```
  rtc wall-clock self-test: first read Ok(RtcTime { second: 10, minute: 57, hour: 8 }) (decoded via the hardware's actual mode bits, fields in valid ranges) -- OK
  rtc wall-clock self-test: wall-clock reads decode via the hardware's reported modes and order monotonically -- OK
  experimental scheduler termination proof: task J walked RUNNING -> TERMINATING -> TERMINATED and was never re-dispatched (ran exactly 1 time) -- OK
  experimental scheduler termination self-test: 1 terminated task(s) (reached via the legal path), TERMINATED unreachable directly from CREATED/READY, remaining tasks active -- OK
  experimental ipc blocking-receive self-test: bounded receive on an empty mailbox returned ReceiveTimedOut (typed, never an indefinite block) -- OK
  experimental ipc blocking-receive self-test: bounded receive delivered the message sent before it (Ok(IpcMessage { sender: 0, seq: 7 })) -- OK
  experimental ipc self-test: arbiter + revocation + blocking-receive contract checks -- OK
PASS: kernel reached BootPhase::Ready and exited cleanly
```

**2026-10-11 gap-closure increment: the first NPS-002/NPS-003
conformance increments** (the governance unblock: AG decision-log rows
I1/I2 landed the specs, the same session's code landed the first
conformance policy):

- **Enforcing NPS-002 §5 process lifecycle** (`experimental::scheduler`):
  `LifecycleState` (the exact §5 state set), one enforcing `set_state`
  through which every dispatch/yield animates, illegal transitions
  refused-and-counted, and an every-boot edge-table self-test checking
  all 36 from→to pairs (9 legal accepts, 27 illegal refusals, the
  rejection counter ticked exactly once per refusal). The positive half
  is the A/B/isolated demos' existing counters reaching their asserted
  values through the enforced path. Note an honest bug this increment's
  own boot test caught: the dispatcher originally selected Ready-only,
  and task 0 -- born Created because it predates the lifecycle --
  could never be re-selected after yielding, hanging the boot in an
  A/B loop; fixed by giving task 0 the state the CPU is factually in
  at init (Running), which restored the exact round-robin the existing
  self-tests assert.
- **NPS-003 §5.4 kernel-as-arbiter + §4.3 revocation**
  (`experimental::ipc`): `send` first validates the claimed `sender`
  against the actually-running task (`scheduler::current_task`),
  refusing a forgery with `NotRunningTask` BEFORE any queue state is
  read (so forgery can't error-probe backpressure), `revoke_sender`
  maintains the forward-looking §4.3 revocation register with typed
  `Revoked` errors and queued-past-revocation receivability, and an
  every-boot self-test pins the whole contract against a scratch
  mailbox (honest send OK; forged refused; post-revocation refused;
  queued-before-revocation receivable; re-revocation an idempotent
  no-op).

Verified transcript lines (from `tools/qemu_boot_test.sh`, passing):

```
  experimental scheduler lifecycle self-test: all 6 transitions attempted from Created matched the edge table (accepted iff legal) -- OK
  experimental scheduler lifecycle self-test: all 6 transitions attempted from Running matched the edge table (accepted iff legal) -- OK
  experimental scheduler lifecycle self-test: 36/36 from-to pairs verified (9 legal accepts, 27 illegal refusals, counter ticked exactly once per refusal) -- OK
  experimental ipc arbiter self-test: honest send from the running task -- OK (queued)
  experimental ipc arbiter self-test: forged sender (claims 1, running 0) refused with NotRunningTask -- OK
  experimental ipc revocation self-test: send after revocation refused with Revoked -- OK
  experimental ipc revocation self-test: message queued before revocation still receivable after it -- OK
  experimental ipc self-test: arbiter + revocation contract checks -- OK
PASS: kernel reached BootPhase::Ready and exited cleanly
```

The pre-existing list continues unchanged:

- A true higher-half address space layout -- the heap's virtual
  address is a manually chosen high address, not a systematic
  kernel/user address-space split (Phase 2 documented simplification)
- Guard-page coverage for every live kernel stack -- once listed here
  as an open gap, now **closed** (2026-10-09). Both the ring-3 tasks'
  RSP0 stacks and the cooperative scheduler's ring-0 task stacks come
  from `memory::kernel_stack`'s shared guarded region, and both are
  verified structurally every boot. (The ring-3 *user* stacks also get
  implicit guard behavior -- the page below each user stack is simply
  unmapped in the process's private address space -- but that is a side
  effect, not a built guard.)
- ACPI-reclaimable memory reuse: once listed here as an open gap, now
  **closed** (2026-10-09). `src/acpi.rs` validates the RSDP the loader
  handed off and enumerates the RSDT/XSDT tables (checksum-verified),
  and the frame allocator only accepts `AcpiReclaimable` regions once
  `reclaim_acpi` has been called -- default posture is "refuse", not
  "assume free". **Honest limitation of the evidence:** every QEMU
  machine type this kernel has booted on reports *no* `AcpiReclaimable`
  region, so the real release path is unexercised here; the allocator's
  release contract is instead proven every boot by a clearly-labelled
  synthetic control on a reserved range (refused -> usable on release ->
  refused again when restored). The RSDP validation and table
  enumeration are real, every-boot evidence. Nothing in this kernel
  *interprets* ACPI tables yet (no MADT/APIC, no FADT, no AML): if a
  later subsystem needs one, it must copy the table before the reclaim.
- Any actual scheduling policy beyond the pre-normative proofs:
  capability-scoped task isolation, fairness, and any *policy* deciding
  when or whether preemption applies. (The timer-driven preemption
  *mechanism* for ring-3 tasks is now built and proven -- see the
  2026-10-09 increment above -- but nothing governs it: no priorities,
  no budgets, no fairness rules.) ~~Specifically NPS-002-gated
  (Draft, benchmark-blocked)~~ **Update 2026-10-11: NPS-002 was
  Accepted (AG row I1) -- the gate is gone; the enforcing §5
  lifecycle landed as the first policy increment the same day, and
  the remaining policy work (capability-scoped isolation, fairness,
  preemption rules) is now open conformance work, not blocked.**
  See `src/experimental/mod.rs`'s banner and
  PHASES.md Phase 3 for the precise split between what's done
  (the switch mechanism + the lifecycle) and what isn't (any
  real policy)
- Interrupt handling beyond the four lines now proven (timer IRQ0,
  RTC IRQ8, keyboard IRQ1, ATA IRQ14/15 -- all unmasked with real
  handlers, see above): every *other* IRQ line is still masked, and
  unmasking a line with no handler wired up is worse than not
  supporting it. Reading the RTC's actual wall-clock *time* (as opposed
  to its periodic tick) ~~is also not implemented -- that needs the
  CMOS time registers' BCD/binary and 12/24-hour modes, deliberately
  left out of the periodic-interrupt module~~ **Update 2026-10-11: closed
  -- `rtc::read_time` decodes per Status B's actual mode bits with a
  bounded UIP wait, proven every boot (see the second-round increment
  above)**
- Any actual IPC policy beyond the pre-normative mailbox proof of
  concept: capability-scoped transfer validation, kernel-as-arbiter
  semantics, blocking receive, or anything resembling NPS-003's
  eventual transport requirements. ~~Specifically NPS-003-gated (Draft,
  transport-pending)~~ **Update 2026-10-11: NPS-003 was Accepted
  (AG row I2) -- the gate is gone; the §5.4 arbiter identity check +
  §4.3 revocation register landed as the first policy increment the
  same day, and the remaining policy work (§5.1 capability-transfer
  objects, blocking receive) is now open conformance work, not
  blocked.** See PHASES.md Phase 4 for the same split as
  Phase 3
- Real dynamic linking -- `experimental::elf::load` now handles
  `ET_DYN`/PIE via `R_X86_64_RELATIVE` relocations (see above), but
  symbol-resolved relocation types (`R_X86_64_GLOB_DAT`,
  `R_X86_64_JUMP_SLOT`, ...), symbol table parsing, string table
  parsing, and resolving symbols against other loaded objects are not
  implemented -- a binary requiring any of that is correctly rejected,
  not silently mishandled. `PT_GNU_RELRO`'s "make read-only again
  after relocating" step ~~is also not implemented~~ **Update
  2026-10-12: closed -- `elf::load` now implements the full
  grant/revoke cycle (see the third-round increment above)**.
  **Update 2026-10-13: partially closed — the in-object half landed**
  (symbol-resolved `GLOB_DAT`/`JUMP_SLOT`/`64` relocations,
  DT_HASH/DT_SYMTAB/DT_STRTAB parsing, and by-name SysV lookup —
  see the fourth-round increment above, `experimental::elf_dyn_demo`).
  **Update 2026-10-13 (same day, second increment): the cross-object
  half landed for the registry scope** — an `SHN_UNDEF` symbol now
  RESOLVES against earlier-loaded objects via the
  `elf::SymbolRegistry` (documented self-own-dynsym-first, then
  load-order-first-match search), proven end to end by a real .so
  PAIR (`experimental::elf_dyn_cross_demo`; see the fifth-round
  increment above). ~~What remains open is beyond the registry's
  scope: `DT_NEEDED`-driven dependency discovery, interposition
  beyond plain load order, lazy PLT binding (JUMP_SLOTs bind eagerly,
  `-z now`-style — no .plt-resolver trampoline), and versioned symbol
  requirements (`DT_VERDEF`/`DT_VERNEED`)~~
  **Update 2026-10-14: three of those five closed — see the
  sixth-round increment above**: the `DT_NEEDED`/`DT_SONAME` contract
  IS enforced (typed `MissingDependency` refusal before any
  relocation work), versioned symbol requirements ARE processed
  (`DT_VERSYM`/`DT_VERDEF`/`DT_VERNEED`, typed `VersionNotProvided`
  when a candidate fails the version/file check), and lazy PLT
  binding IS real (a trap-based first-use binder, IDT `0x88`, proven
  to bind exactly once across repeated calls).
  ~~What REMAINS open is what genuinely needs surfaces the kernel does
  not yet have: filesystem discovery/loading of a `DT_NEEDED` FILE
  (Phase 8: needs storage), interposition beyond the documented
  load-order registry rule, and GNU-`ld.so`-grade resolver internals
  (a .plt resolver with its own tables, hash-table alternatives like
  DT_GNU_HASH, symbol-merging semantics)~~
  **Update 2026-10-14 (see the seventh-round increment above): two of
  those closed by rule — recursive `DT_NEEDED` discovery through the
  named `DepCatalog` surface (the file-system client, when Phase 8
  lands, replaces the catalog's embedded-file entries and NOTHING in
  the loader changes shape), and interposition beyond load order via
  the explicit `SymbolRegistry::interpose` overlay. Weak-versioned
  semantics (`STB_WEAK`/`VER_FLG_WEAK`) are also in as of that
  round: tolerated-unresolved means the NULL slot, never a guess.**
  **Update 2026-10-15 (eighth round): DT_GNU_HASH is IN — parsed and
  looked up glibc-style (bloom + bucket/chain), as a separate
  mechanism from the SysV walk with the two answers' equality checked
  every boot, GNU preferred on `--hash-style=both` objects while
  DT_HASH keeps its authority (the symbol count and the GNU table's
  undefined-symbol exclusion). The resolver is PLURAL about hash
  tables now; ~~what remains open is genuinely GNU-`ld.so`-grade resolver
  internals: a userland .plt resolver with its own hash tables (the
  kernel's lazy binding is trap-based and kernel-arbitrated —
  semantics-identical first-use binding, different mechanism, a
  deliberate scope choice), and full symbol-merging semantics across
  unregistered scopes.~~
  **Update 2026-10-17 (ninth round): the GNU OWN-`.plt` lazy protocol
  IS IN — `elf::arm_own_plt`, armed via
  `LoadContext::own_plt_resolver_page` (`experimental::elf_dyn_plt_demo`
  proves it end to end in ring 3).** For an own-armed object the
  loader emits NO synthetic `int 0x88` trampoline pages and instead
  hands the object's REAL linker `.plt`/`.got.plt` machinery over to
  the GNU protocol: `DT_PLTGOT` required up front (typed
  `MalformedDynamicTables` otherwise, before any relocation work);
  GOT[1] := a per-object LINK-MAP ID (an integer cookie the registry
  owns — deliberately NOT a user-writable kernel pointer, this
  kernel's honest analogy to the GNU `struct link_map*`); GOT[2] := a
  user-executable resolver thunk (`int 0x8B`, a NEW ring-3-privileged
  gate installed as a RAW handler whose body restores EVERY user GPR
  before the guided `iretq` — the `x86-interrupt` ABI's register
  restore is bypassed by any frame-guiding return, and a naked-entry
  blob+pop scheme like `ring3_scheduler`'s is what keeps the user
  state intact); every JUMP_SLOT slot := the linker's own baked
  initial `plt_stub+6` value ADJUSTED BY THE LOAD BIAS (exactly GNU
  `elf_machine_lazy_rel`'s lazy relocation — the file bytes carry
  pre-bias, the runtime slots carry live state); and the GOT[1]/GOT[2]
  arming writes happen INSIDE the GNU_RELRO grant window, GNU-ld's
  own order (the first three `.got.plt` slots sit inside the region by
  linker layout), revoked read-only with everything else. First use:
  PLT1's `push $idx` and PLT0's `push GOT[1]` put the REAL spill
  (cookie, index, caller ret) on the user stack;
  `lazy_binder::plt_service_core` identifies the object BY THE COOKIE
  (new typed `UnregisteredLinkMap` refusal + its
  `probe_unregistered_lm` negative control), resolves through the
  SAME registry + version contract as every other path, patches the
  REAL slot, and then GUIDES the return the way GNU
  `_dl_runtime_resolve` ends: `rsp += 16` drops ONLY the two
  callee-side spilled words and execution resumes INSIDE the resolved
  callee, whose own `ret` reaches the original caller — every-boot
  facts: exactly ONE bind across three calls PER OBJECT, the slot
  patched to the resolved cross_fn, three correct eax results read
  from the dependent's own page, and the SERVICE-level GNU facts:
  the SAME dependent artifact loaded TWICE at two bases with two
  distinct, monotonically-allocated link-map cookies — each bound
  EXACTLY once through ONE gate (0x8B); and the MULTI-ENTRY GNU
  artifact (userprog_multi.elf, linked against BOTH providers):
  its `.rela.plt` carries TWO R_X86_64_JUMP_SLOT entries, ONE
  link-map cookie takes EXACTLY TWO first-use binds (one per
  entry, each resolved by ITS OWN index), both slots patch to
  their respective resolved addresses, per-entry laziness held
  (the third fn_b call is direct) — one registry serving the GNU
  instances (5 objects / 6 binds: 2 trap-stub + 1 + 1 + 2 per
  boot), plus CI-gate facts pinning PLTGOT/JMPREL/PLTREL=RELA,
  the multi provider's SONAME and its dependent's NEEDED pair,
  the PLT-LESS boundary artifact's load-bearing ABSENCE (zero PLT
  dynamic tags, gate-checked directly), and the new scratch window.
  The GNU protocol is OPT-IN PER LOAD and refuses its own
  machinery's absence: requested on the PLT-LESS artifact it is a
  typed `MalformedDynamicTables` refusal before any lazy work, and
  the SAME artifact loads eagerly in the plain resolver mode — its
  GLOB_DAT resolves AT LOAD, relro revoked read-only, ZERO GNU
  arming side effects (registry 5 -> 5, binds 6 -> 6, no thunk
  page) — eager stays the default for objects that never had a
  PLT.
  What GENUINELY remains open of the GNU-`ld.so` internals: a
  ring-3-EXECUTABLE resolver code body with its own private hash
  tables (the resolution itself stays kernel-arbitrated
  `resolve_lazy_entry` through the registry — the WIRE protocol is
  GNU-shaped, the resolver is not), and full symbol-merging semantics
  across unregistered scopes.** Unresolvable requirements are STILL REFUSED
  with typed errors (`UndefinedSymbol`/`MissingDependency`/
  `VersionNotProvided`/`DependencyCycle`) except the weak rule's
  NULL-slot case / surfaced as `RelroOutcome::Denied`, never
  mis-resolved.
- Address-space teardown for a space that was ever the *active* one
  for a ring-3 trap: once listed here as an open sharp edge (the
  documented caveat in `AddressSpace::destroy`'s own safety comment),
  now **closed** (2026-10-09). `ring3_scheduler`'s finale destroys task
  F's address space *after* F was the active space for its own seven
  `int 0x81` yields and has been suspended for good -- the exact
  precondition `destroy` demands -- and verifies the frames genuinely
  came back (15 frames reclaimed). The remaining demos (`isolated_tasks`,
  `isolation_demo`, `pie_demo`, and E/G/H/I in `ring3_scheduler`) still
  `mem::forget` on purpose; only the ever-active case needed proving,
  and it now is. (TLB invalidation on unmap -- once listed here as an
  open gap -- is also now implemented: `memory::paging::unmap_page`
  emits `invlpg` via the mapper's flush, proven by `memory::tlb_demo`.)
- Real Windows application compatibility -- `experimental::pe` loads a
  PE32+ binary's own code and data with correct section permissions,
  nothing more. No import resolution (DLL calls do nothing); base
  relocation processing *is* implemented for the relocation types a
  self-contained PE32+ uses (`IMAGE_REL_BASED_DIR64`/`HIGHLOW`),
  proven by `pe_reloc_demo` loading a relocatable PE at a
  loader-chosen address -- an unsupported relocation type is rejected
  rather than mishandled. ~~Most real-world Windows binaries
  additionally need imports, so they still won't run.~~ No Win32 API
  surface, no resources, no exception tables. **Update 2026-10-19: the
  import-resolution half is IN** (`experimental::pe_import_demo` -- see
  the 2026-10-19 increment above): a real DLL's own export directory
  selects both exports (the real GetProcAddress mechanism), a real
  Windows-format client linked directly against it has its IAT
  kernel-resolved through its REAL import directory, and its own ring-3
  code consumes both a data export and an indirect call through the
  resolved slots, with two typed negative refusals checked every boot
  (empty registry -> `MissingDllImport`; DLL name bound to the wrong
  file -> `MissingImport`). ~~No import resolution (DLL calls do
  nothing);~~ **Update 2026-10-19, same day (second increment): the
  last two GetProcAddress halves are in too** -- forwarded exports
  chase through the registry depth-capped (typed
  `MalformedForwarder`/`ForwarderDepthExceeded`/`MissingDllImport`
  refusals, every boot) and ordinal imports resolve through the
  directory's own parsed `OrdinalBase` with the named-vs-ordinal
  cross-path equality checked every boot (see the second increment
  above). **Update 2026-10-24: exception tables and resource
  directories are IN too** (see the two 2026-10-24 cards above: the
  startup `IMAGE_DIRECTORY_ENTRY_EXCEPTION` and
  `IMAGE_DIRECTORY_ENTRY_RESOURCE` walks, each with its composed
  carrier family and typed per-rule refusals, every boot, both
  toolchains). What still does not exist: Win32 API surface -- **Update
  2026-11-21: the FIRST four Win32 functions now do exist** (see the
  Win32 API surface v0 section below: `GetStdHandle`, `WriteFile`,
  `GetTickCount`, `ExitProcess`, performed by the kernel for a real
  Windows console program through a real PE32+ `kernel32.dll`); what
  remains is the other ~99.9% of the API and the NT system-call layer
  beneath it
- Real Android application compatibility -- `experimental::dex` parses
  a DEX file's container format and validates its structure (including,
  now, its Adler-32 checksum and SHA-1 signature via
  `experimental::crypto`), nothing more. No bytecode interpretation or
  AOT compilation (no ART/Dalvik semantics of any kind), and no APK
  (ZIP) container unwrapping -- this parser handles a standalone `.dex`
  file, not one extracted from a real `.apk`
- Preemptive (timer-driven) ring-3 task switching -- once listed here
  as an open gap ("the round-robin now handles three tasks (E, F, G),
  but is still cooperative-only: a ring-3 task must itself `int 0x81`
  to yield, nothing forces it to"), now **closed** (2026-10-09). The
  E/F/G round-robin deliberately stays cooperative (its exact-count
  assertions need complete yield cycles); the preemption proof is its
  own phase -- two non-yielding tasks (H, I) round-robined purely by
  IRQ0. See the increment above. What deliberately does *not* exist is
  any preemption *policy* (NPS-002 is now Accepted -- AG row I1,
  2026-10-11 -- so policy here is open conformance work rather than
  blocked; see the Phase 3 entry in PHASES.md)
- A per-container root filesystem, `pivot_root`, or any storage
  integration
- GPU command submission path (NPS-001 §3's one kernel-space
  exception) -- not started
- Windows or Android compatibility -- not started; scope for these is
  now defined precisely in RFC-0001 §4 and `PHASES.md` Phase 5/6
- ~~UEFI boot~~ **UEFI native boot is IN** (2026-10-22 increment: the
  stub loads the kernel from its own bytes, synthesizes MBI, hands off
  through `ExitBootServices` + CR3 switch; both boot gates are CI
  scripts -- see the 2026-10-23 card). **Secure Boot state is now
  read AND its protocol proven every boot** (2026-10-25 Phase 10 card:
  the variable-services round trip pins every GetVariable/SetVariable
  answer `read_secure_boot` relies on, before anything is trusted;
  ADR-0014 keeps an SB-off boot legal -- the SecureBoot fact is
  recorded, never enforced as policy). Phase 1 no longer boots
  BIOS-only: both the GRUB/Multiboot2 path and the native OVMF stub
  path boot the full demo chain

## Limitation disposition (2026-10-09): why each remaining item is not fixed here

**2026-10-11 UPDATE -- the governance category below is now EMPTY.**
NPS-002, NPS-003, and ADR-0014 were all **Accepted 2026-10-11** (AG
decision-log rows I1/I2/I3, owner direction via the recorded session --
see `docs/00-platform/AG_AGENDA.md`). The first conformance increments
against the now-binding specs landed the same day: an enforcing NPS-002
§5 process-lifecycle state machine in `experimental::scheduler` (36/36
edge-table pairs verified every boot), and the NPS-003 §5.4
kernel-arbiter identity check + §4.3 endpoint revocation register in
`experimental::ipc` (forged-sender and post-revocation sends refused,
queued-past-revocation still receivable, all every-boot). The two
"Blocked by governance" bullets below are retained as the historical
record of why the policy work waited until the specs were binding --
the posture change itself is recorded in `src/experimental/mod.rs`'s
banner and `PHASES.md`'s Phase 3/4 sections.

---

*(Historical record, 2026-10-09: why each remaining item was not fixed
then.)*

The request that drove the recent increments was to process every
documented limitation. Some are genuinely fixable and were fixed (TLB
on unmap, DEX integrity, three ring-3 tasks, guard pages, the RTC IRQ,
the keyboard/ATA IRQ lines, timer-driven ring-3 preemption).
The rest fall into clear categories, and pretending otherwise would be
the opposite of this repo's honest-scope posture. Stated explicitly:

**Blocked by governance (must stay pre-normative) -- [RESOLVED
2026-10-11, see the update above]:**

- *Scheduling policy* (capability-scoped isolation, fairness, rules
  governing preemption) -- NPS-002 was `Draft`, benchmark-blocked at
  §9. Implementing it as if binding would have violated NPC-001 §5.
  The switch *mechanism* was done -- including timer-driven preemption
  of ring-3 tasks, added 2026-10-09 as explicitly pre-normative proof.
  The spec is now Accepted (row I1), and the first policy increment
  (the enforcing §5 lifecycle) is landed and boot-verified.
- *IPC policy* (capability-scoped transfer validation, kernel arbiter,
  blocking receive) -- NPS-003 was `Draft`, transport-pending at §6.1.
  Same reasoning; the mailbox mechanism was done, the policy was not.
  The spec is now Accepted (row I2), and the first policy increment
  (the §5.4 arbiter + §4.3 revocation) is landed and boot-verified.

**Blocked by a Proposed ADR -- [RESOLVED 2026-10-11, AG row I3]:**

- *UEFI boot / Secure Boot* -- ADR-0014 is `Proposed`, not `Accepted`.
  Phase 1 boots via legacy BIOS/GRUB2/Multiboot2 only.

**Genuinely multi-month scope -- out of scope for a first-release
kernel, and documented as such rather than half-attempted:**

- *Win32 API surface + PE import resolution* -- loading a PE32+'s own
  code (now including its base relocations) is a loader; Windows
  *application* compatibility is a full user-mode API translation
  layer. Different order of magnitude.
  **Update 2026-10-19: the PE-import-resolution half is landed**
  (`experimental::pe_import_demo` -- a real DLL's export directory, a
  real client's kernel-resolved IAT, both exercised in ring 3 -- see
  the increment above), **and updated the same day: forwarded exports
  and ordinal imports are in too** -- the resolver is GetProcAddress-
  complete; what remains here is ONLY the Win32-shaped user-mode API
  translation layer, still a different order of magnitude.
- *DEX bytecode execution (ART/Dalvik semantics)* -- parsing the DEX
  container (done) is not running the bytecode; the latter is
  comparable in scope to writing a JVM.
- *Real dynamic linking for ELF* (`R_X86_64_GLOB_DAT`,
  `_JUMP_SLOT`, symbol/string tables, cross-object resolution,
  `PT_GNU_RELRO`) -- a dynamic linker is its own subsystem.
  (2026-10-13: the in-object and registry-scoped cross-object
  halves above are landed; the `ld.so`-grade remainder noted there
  still is.)
- *APK (ZIP) container unwrapping + DEFLATE* -- the DEX parser handles
  a standalone `.dex`; a real `.apk` needs ZIP central-directory
  parsing and an inflate implementation to get at a compressed
  `classes.dex`.
- *True higher-half layout, physmap window, per-container rootfs /
  `pivot_root` / storage, GPU command submission* -- architecture-level
  work; the current identity-map simplification is documented in
  `memory::paging` and `experimental::address_space`.

**Tractable and *not* governance-gated:**

- No open items remain in this category. The last two are now done:
  more IRQ lines with real handlers (keyboard IRQ1, `src/keyboard.rs`;
  ATA/ATAPI IDE channels IRQ14/15, `src/ata.rs`) and the last open
  *mechanism* candidate, timer-driven ring-3 preemption (both
  2026-10-09 increments above). The rule from
  `pic.rs` still stands for any future line: unmask nothing that has
  no handler.

*(Address-space teardown for an ever-active ring-3 space, once listed
here, is now done -- see the closed bullet in "What does NOT exist
yet" above and the 2026-10-09 increment.)*

## Toolchain notes (read before touching the build)

**Current setup: rustup stable (rustc 1.99.1) + `rust-src`, with the
`x86_64` crate pinned `=0.15.5` and `rust-version = "1.99.1"` recorded
in `Cargo.toml`.** The rustc/crate pairing is part of the contract, not
two independent pins: the crate's `core::iter::Step` impls tracks
rustc's UNSTABLE trait shape, which has now changed three times (see
`Cargo.toml`'s dependency comment and the migration record below), so
neither half floats. The build needs the standard `rustc`/`cargo`, the
`rust-src` component (so `-Z build-std` can rebuild `core`/`alloc` for
the freestanding target), and a `rust-lld`. `../../setup.sh` installs all of that on a fresh
Debian/Ubuntu host (idempotent; `SUDO_PASSWORD` for non-interactive
sudo) and then runs the two commands below; manually it is:

```bash
rustup component add rust-src          # provides the sysroot core/alloc sources
./build.sh                             # self-contained: finds rust-lld for you
./tools/qemu_boot_test.sh              # build + boot + real pass/fail exit code
```

`build.sh` sets `RUSTC_BOOTSTRAP=1` for the cargo invocation. This is
not a project-specific hack -- it is the same environment variable
rustc's own build system (`x.py`) uses to let a *stable* compiler build
the next compiler with unstable features. Here it lets stable cargo
accept two unstable flags this freestanding build needs:
`-Z build-std=core,alloc` (no precompiled `core`/`alloc` exists for the
custom target) and `-Zjson-target-spec` (required by current cargo to
accept the `.json` target in `.cargo/config.toml`; older cargo accepted
a bare `.json` target without it).

Other host requirements, all satisfied here: GNU `as`/`ld` (`binutils`)
for the assembly boot stub, `grub-mkrescue`/`xorriso` for the ISO, and
`qemu-system-x86_64` for the boot test. `rust-lld` ships inside the
rustup toolchain's sysroot `bin` directory (not on `PATH` by default);
`build.sh` locates it automatically, falling back to a distro `rust-lld`
if one is already on `PATH`.

### Toolchain migration (2026-10-08): apt 1.75 -> rustup stable 1.97

The original bring-up used Ubuntu's `apt` `rustc` 1.75.0 with
`rust-src` from `/usr/src/rustc-1.75.0`. That toolchain is no longer
usable in this environment (its `rust-src` path is absent, and the code
below now requires features stabilized after 1.75), so the kernel was
migrated to the rustup stable toolchain (1.97.1). The migration was
mechanical but touched five real incompatibilities, each fixed at its
cause rather than worked around:

1. **`.json` target specs now require `-Zjson-target-spec`.** Added to
the `cargo` invocation in `build.sh`.
2. **The target-spec schema tightened.** `target-pointer-width` /
`target-c-int-width` must be numbers, not strings; `x86_64-soft-float`
now needs an explicit `"rustc-abi": "softfloat"` and the full
`-sse*`/`-avx*` disable list (`x86_64-nyrqis-kernel.json` updated to
match the built-in `x86_64-unknown-none` feature set).
3. **`#[naked]` became `#[unsafe(naked)]`, and naked bodies require
`naked_asm!`** (plain `asm!` is rejected in a naked function, and
`options(noreturn)` is no longer accepted -- `naked_asm!` is
implicitly noreturn). Updated in `experimental/{scheduler,
ring3_scheduler,usermode}.rs`.
4. **`naked_functions` is stable** (since 1.88), so that `#![feature]`
gate was removed from `lib.rs`.
5. **`x86_64` crate bumped `=0.14.10` -> `=0.14.13`.** The crate's
`core::iter::Step` impls track rustc's `steps_between` signature, which
moved from `Option<usize>` to `(usize, Option<usize>)` between 0.14.10
and 0.14.11 -- current stable expects the new form (the mirror image of
bug 1 in the Phase 1 log below, which pinned 0.14.10 for the *old*
form).

### Migration (2026-10-23): rustc 1.97.1 -> 1.99.1, `x86_64` =0.14.13 -> `=0.15.5`

Same-day follow-up to the CI-pin section below: rather than leaving
the toolchain contract frozen at the aging 1.97.1, the sixth point it
called for was EXECUTED. First, the boundary was established from
both sides rather than assumed: under 1.99.1, `=0.14.13` dies in
`-Z build-std` with the known `E0046` pair (the new
`forward_overflowing`/`backward_overflowing` required items it does
not implement), and under 1.97.1, `=0.15.5` dies with `E0407`
(`method forward_overflowing is not a member of trait Step` -- the
crate implements exactly the post-1.99 shape, whose older traits
carry those as provided-free items that rustc now REMOVED). No crate
version satisfies both rustc shapes, so the migration is upgrade-only
by construction and the verified floor moves to 1.99.1 -- recorded as
cargo's `rust-version` in `Cargo.toml`, so an older toolchain now
fails at configure time with a plain message instead of a cryptic
error inside a registry crate. The `=` pin survives: `=0.15.5` is
exact (the newest release; 0.15.3+ is the smallest line tracking the
post-1.99 `Step` surface).

The kernel-side fallout was contained to four API areas, each fixed
at its cause:

1. **IDT entries index by `u8`.** 0.14's index-into-the-table sites
   took the vector consts as they happened to be typed; 0.15's
   `InterruptDescriptorTable` indexing and `set_handler_fn` plumbing
   are `u8`-shaped. The five vector consts (`TIMER_VECTOR`,
   `RTC_VECTOR`, `KEYBOARD_VECTOR`, `ATA_PRIMARY_VECTOR`,
   `ATA_SECONDARY_VECTOR`) became `u8`-typed and every
   `as usize]`-cast index site in `src/idt.rs` was updated to match
   (twelve sites).
2. **`GDT::add_entry` is `GDT::append` now** (the new name states the
   slot-ordering invariant the old name left implicit):
   `src/gdt.rs`, four call sites.
3. **`SegmentSelector` construction changed shape** (the 0.15 API
   builds rpl/bits through typed methods instead of raw bit twiddling):
   `src/gdt.rs` and `src/experimental/ring3_scheduler.rs`.
4. **`Cr2::read` returns `Result<VirtAddr, VirtAddrNotValid>`, not a
   bare `VirtAddr`** -- a CR2 read can carry a non-canonical address,
   and the type now makes the caller decide. The page-fault handler
   in `src/idt.rs` unwraps with `expect`: an invalid CR2 while
   REPORTING a page fault is itself a fatal machine state, so the
   honest handling is to panic loudly rather than guess at an address.

Verified end to end: clean kernel build under 1.99.1 (debug), then
BOTH boot gates plus the transcript-parity gate through `build.sh`
-- all three PASS, guest transcripts byte-identical to the 2026-10-23
records above, because the migration touched names and types, not
behavior. The `E0407` under 1.97.1 is recorded as the boundary
evidence, not a bug; the floor (`rust-version`) is the artifact that
keeps future contributors out of it.

### CI toolchain pin (2026-10-23, twice): `@1.97.1` first, then `@1.99.1` after the same-day migration

The new `kernel-boot` CI job's first run failed exactly where this
contract says it would if left floating: `dtolnay/rust-toolchain
@stable` drifted to stable 1.99.1 (released 2026-10-13), and 1.99
tightened `core::iter::Step` by requiring the new
`forward_overflowing`/`backward_overflowing` trait items -- which the
then-pinned `x86_64 =0.14.13` crate does not implement -- so the
`-Z build-std` rebuild of `core` died with the crate's `E0046` errors
(cargo exit 101) on the runner, while the dev host -- still on 1.97.1
-- kept passing. Reproduced locally by installing 1.99.1 and rerunning
`./build.sh --qemu-test` under it: the identical E0046 pair on
`x86_64-0.14.13/src/addr.rs` and `.../structures/paging/page.rs`,
confirming the drift diagnosis rather than a guess. The first fix was
a pin to `rust-toolchain@1.97.1` (+ `rust-src`), matching the
then-current contract.

The same-day migration below (0.14.13 -> `=0.15.5`, 1.97.1 -> 1.99.1)
then made that pin obsolete and it was raised: CI now runs
`rust-toolchain@1.99.1` (+ `rust-src`), the exact pairing re-verified
end to end near the top of this section. A pin survives, because the
failure mode it guards is real in BOTH directions: a floating `@stable`
quietly E0046'd the gates the morning stable hit 1.99.1 against
0.14.13, and `=0.15.5` E0407's under everything before 1.99 -- so a
drifted pairing fails cryptically inside a registry crate instead of
loudly at a documented contract, and cargo's `rust-version = "1.99.1"`
floor now turns the known-unsupported direction into a clear message
at configure time. Raising the pin again is THIS DOCUMENTED KIND OF
WORK: extend this section's walk (1.75 -> 1.97 -> 1.99), bump `x86_64`
to the version implementing the next `Step` shape, re-verify both boot
gates plus the parity gate under the new pairing, and land pin, floor,
and record together -- not let an `@stable` float do it unannounced.

### Runner-image pin (2026-10-23): `ubuntu-24.04` for the kernel-boot job

The same day the CI pin above was raised, the `ubuntu-latest` label
itself began rolling to Ubuntu 26.04.1 (GitHub announced the migration
beginning 2026-10-19), and this failure arrived through a channel no
one had pinned. The wrapper diagnostics (`tools/ci_emit_annotations.sh`
-- job logs are not fetchable through the API without admin rights;
annotations are) showed the failing run's environment against the
local one, with the kernel code UNCHANGED between two adjacent runs
that went green -> red:

- local / QEMU 8.2.2 + GRUB 2.12 + binutils 2.42/2.43: both gates
pass, and the guest transcripts match the records above line-for-line
- runner / Ubuntu 26.04.1 + QEMU 10.2.1 + GRUB 2.14 + **binutils
2.46**: the BIOS gate fails inside the layout-gate's toolchain-pinned
PE fact for `userprog_pe_import_dll.dll` (`[Entry 0
0000000000003000.*Export Directory]`) -- under ld 2.46 (its
`MinorLinkerVersion 46` shows in the emitted PE's own header dump)
the export directory lands at rva 0x4000 instead, pushed by a
toolchain-internal layout change, not by anything the kernel sources
or the patcher scripts do. The kernel side machine-reads the PE data
directories straight out of the image headers, so nothing in the
RUNNING kernel depends on the literal 0x3000 -- the pinned fact
exists to catch UNINVITED layout drift, and it caught exactly that.
The gate failed BEFORE any guest boot, so the newer QEMU/GRUB are
catalogued-but-not-yet-implicated territory for the next pass.

Response: the `kernel-boot` job is pinned to `runs-on: ubuntu-24.04`,
the configuration the entire verification record (both gates, the
parity transcripts, the line counts above) was built against. Raising
THAT pin is the same documented kind of work as the rustc one: re-run
both gates plus parity under the 26.04 tooling, catalogue (or pin
away) the binutils 2.46 layout delta for the PE artifacts, and land
record, pin, and gates together -- not drift unannounced.

The diagnosing channel carries two implementation lessons, both proven
by real runs (each commented at the emitting code): the gate-log tail
must be flattened to ONE blob before wrapping, because `fold` never
merges input lines -- the naive form emits hundreds of annotations and
GitHub silently drops everything past the ~10-per-step cap; and
~3600-char single-line annotations were dropped server-side, so
chunks ship at ~1000 chars. The wrapper also needed `mkdir -p target`
before its redirects: a fresh runner checkout has no gitignored
target/, and a failing redirect makes `set +e`-captured `$?` fail the
step BEFORE the gate ever runs (the "BIOS gate failed" that was
actually bash's redirect error).

After the migration the kernel **builds clean (debug + release) and the
full `qemu_boot_test.sh` chain passes** -- every prior demo's self-test
is unchanged, so the migration introduced no behavioral regression. As
of the same day (2026-10-24), the kernel's warning set is ZERO, cleared
at cause rather than silenced:

- **83 `static mut` reference sites** (the Rust-2024
`static_mut_refs` lint) migrated to the `&raw` forms: mutable-taking
shapes read `(*&raw mut STATIC).method(...)` (or, where `Vec`
autoref into the deref would itself create a raw-pointer-target
reference -- a HARD error under 1.99.1, not a lint -- the explicit
ref the compiler suggests: `(&mut *(&raw mut STATIC))`), const-taking
shapes read `(*&raw const STATIC).method(...)`. No `#[allow]`s
anywhere; every demo/registry accesses the same statics through
pointers the compiler can trace.
- **one atomic decl converted to its interior-mutability form**:
`NEXT_LM_ID` was an `AtomicU64` behind `static mut`; fetch-add IS the
mutability, so it is now a plain `static` and its uses are safe code.
- **one dead `const` made load-bearing**: the loader's `SHN_UNDEF`
was never named (sites compared `st_shndx == 0` with a literal);
all seven comparisons now name the const instead.
- **one unreachable statement removed**: after `terminate_current()`
(`-> !`), the belt-and-braces `unreachable!("...")` in
`experimental_terminating_task` was trivially dead; the tail-diverging
call REPLACES it (which also type-checks the `-> !` contract).
- **one deprecated fn-item cast fixed**: `ring3_yield_trap_entry as
u64` now routes through a raw pointer (`as *const () as u64`), the
form the `function_casts_as_integer` message itself asks for.

Both gates plus the parity gate re-verified zero-warning afterwards.

### binutils 2.46 surface catalogued (2026-10-24): kernel-boot un-pinned

The `ubuntu-24.04` runner-image pin on the `kernel-boot` job is LIFTED
on the strength of a reproduced catalogue, not a hope: the nykernel
was built END TO END on this host under a real binutils 2.46 ld/as/objdump
(the ubuntu-26.04 packages, unpacked locally, since no container runtime
exists here), and `tools/verify_prog_layout.sh` keys every affected
fact on the `ld` actually in PATH, documented per toolchain line:

- **movement deltas (+0x1000, ld 2.46 vs 2.42/2.43)**: the provider
DLL's Export Directory 0x3000 → 0x4000; the import client's Import
Directory 0x2000 → 0x3000; the composed Bound Import Directory
0x2100 → 0x3100 (it composes at `.idata` vaddr+vsize, which moved with
the import block -- the PATCHERS themselves re-derive it, so only the
gate's literals needed keying). Nothing else moved: SizeOfImage,
entry point, the delay carriers' composed 0x3018, and every unsigned
fact held at both lines.
- **format deltas (objdump 2.46 vs 2.42/2.43)**: the Export Address
Table entries gain `Ordinal  Address  Type` header and zero-pad the
RVA (`1000 Export RVA` → `00001000`), and the Name Pointer entries
become `[   0] +base[   1]  0000 crossfn`. `prov_names`' count grep
accepts both spellings so a re-roll moves pinned RVAs, never counts.
- **uncatalogued binutils lines refuse loudly**: the gate prints the
exact FAIL ("no PE-directory catalogue entry for binutils X.Y") plus
the actual objdump surface to extend the catalogue with -- the
same documented kind of work as the rustc pin walk.

===

### QEMU 10.x exit-contract catalogue (2026-10-24): `-no-shutdown` dropped

The boot-side class above landed exactly as predicted, and the
annotation channel named it: on the ubuntu-26 runners (QEMU 10.2.1)
the BIOS transcript reached the chain's LAST line
("phase 1+2+3+4+ring3-elf-isolation-poc+guard-pages complete") and then
sat stopped until the timeout kill ("terminating on signal 15")
-- 15 s first, then a raised 120 s cap proved it was NOT runner
slowness: the guest printed everything and simply never exited.
Cause, verified against qemu master's `hw/misc/debugexit.c`: the
isa-debug-exit write handler no longer calls `exit()` directly -- it
routes through `qemu_system_shutdown_request_with_code(...`, which
HONORS `-no-shutdown` by stopping the VM instead of exiting the
process. Both boot gate scripts carried `-no-shutdown`, inert beside
8.x's direct `exit()`; 10.x turned that inherited flag into the
whole failure. The scripts DROP the flag (catalogued in-place;
`-no-reboot` stays as the reboot-loop guard) -- the exit-code
contract `(value << 1) | 1` is unchanged, the change re-verified
locally on QEMU 8.2 both toolchains (exit 33 still produced).

### The 26.04 tooling round completed (2026-10-25): full parity under real binutils 2.46, gates keyed on both toolchains

The binutils-2.46 round this section documented as NEXT WORK is now
DONE, reproduced end to end on this host and landed as record -- not
catalogued-then-forgotten. The act (the same one the record always
names as the only acceptable response to a toolchain delta):

1. **A real 26.04 toolchain, unpacked locally.** Ubuntu 26.04's actual
binutils packages (2.46-3ubuntu2: `binutils-common`, `libbinutils`,
`binutils-x86-64-linux-gnu`, `binutils`, plus `libsframe3` -- 
2.46's lld-compat driver needs it) were downloaded from the Ubuntu
archive pool and unpacked under `target/toolchain-2.46/` (gitignored;
no container runtime on this host, same as the first 2.46 pass).
`target/toolchain-2.46/root/usr/bin/ld --version` = "GNU ld (GNU
Binutils for Ubuntu) 2.46" -- the exact minor the ubuntu-26 runners
carry.

2. **Full rebuild under 2.46 with the composed artifacts removed
first.** Every PE provider/client/carrier artifact is rebuildable
build output, and the composition patchers refuse
already-composed files by design (idempotence refusal), so the
rebuild starts from `rm userprog/userprog_pe*.exe *.dll` and
re-links everything with 2.46's as/ld first in PATH. The build
produced the EXACT movement deltas the 2.46 card catalogued: the
provider's Export Directory at 0x4000 (not 0x3000), the composed
pdata/res blocks at 0x3018 (not 0x2018/0x2018 -- the import block
moved +0x1000, and the patchers re-derive every address from the
carrier's own section table, so nothing in them needed changes),
and `.text` came out vsize 0x20 (not 0x40 -- 2.46 packs the
single-label client tighter), which moved the pdata entries' spans
with it -- again loc-by-fact, never a literal.

3. **Both gates plus the boot were re-run under the 2.46-built
tree.** `tools/verify_prog_layout.sh` keyed every affected fact on
the ld actually in PATH (its per-version PE-directory catalogue) and
passed line-for-line; `qemu_boot_test.sh` and
`qemu_uefi_boot_test.sh` both PASSed against the 2.46-linked
kernel.elf (local QEMU 8.2 -- the QEMU 10 / 26.04-runner exit
contract is the card above; its fix is in the scripts, so the same
contract holds on 10.x).

4. **The baseline re-proven the same day, for an honest parity
claim.** Back on 2.42/2.43 (the host default), the same
remove-composition + full rebuild produced build exit 0, the layout
gate green, the BIOS gate PASS with **294 `OK`-line facts** in the
transcript (280 before this week; the pdata and resource
demo -- below -- add the delta), and the UEFI gate PASS. Both
toolchains carry the SAME kernel-side behavior and the SAME gate
discipline; the only differences are the catalogued, keyed layout
literals.

Conclusion, stated as the record always states it: `kernel-boot`'s
un-pinned `ubuntu-latest` stays un-pinned -- under 26.04's toolchains
the gate keys correctly, the patchers re-derive correctly, and the
kernel boots identically; the failure that named this work is
catalogued at every one of its causes. The next toolchain that drifts
gets exactly this pass.

## PE increment (2026-10-24): the startup IMAGE_DIRECTORY_ENTRY_EXCEPTION walk -- typed refusals, and a real client whose compliant table verified runs in ring 3

- **Kernel side** (`experimental::pe`, `pe_exception_demo`): the loader
  now walks `DataDirectory[3]` when the optional header declares it.
  The walk re-derives every address from the file's own optional header
  and section table (the single bounds authority, max virtual address
  = VirtualAddress + VirtualSize per section) and reads the runtime
  function array ONLY inside the mapped image's bounded span.
  Per entry: Begin/End addresses must be inside the image and ordered
  (Begin < End), entries must be sorted and non-overlapping against
  the previous entry, and each entry's UNWIND_INFO address must itself
  lie inside the image with a version nibble the x64 ABI defines (0..2
  -- a version 3 nibble is undefined per ABI and refused). Every
  refusal is a typed `MalformedExceptionTable(&'static str)` explaining
  exactly which rule broke.
- **Client side**: `userprog/prog_pe_pdata.s` -- the same one-assembly
  fixture as every other PE carrier (a real minimal PE32+ native
  client built with GNU as/ld only); THREE carriers share it,
  distinguished solely by bytes the composition patcher lays:
  `GOOD.exe`'s two non-overlapping entries cover both halves of its
  `.text` (each naming its own UNWIND_INFO); `MIS.exe`'s second entry
  genuinely overlaps the first entry's range (begin below its end)
  -- a real overlapping table, not a truncated one; `VER.exe` carries
  the undefined version-3 nibble. `tools/patch_pe_pdata.py` composes
  the directory at `DataDirectory[3]` from each carrier's OWN section
  facts (48 bytes: two 12-byte entries + two 8-byte UNWIND_INFOs, laid
  in `.idata`'s in-block alignment padding, with the section's
  VirtualSize grown to cover exactly the composed bytes -- the same
  room rule the bound/delay patchers use), then re-parses the file AS
  WRITTEN and refuses unless the on-disk bytes ARE the composed bytes
  (the same idempotence-refusal posture as every other patcher).
- **The refactor's honest cost**: the second draft of the walk had the
  first shape's disease -- a recursive return that dropped the root's
  facts and one bounds check placed after a read -- so both walk
  functions were rewritten cleanly over a single mutable state struct
  before the first compile, and the first build shook out exactly one
  real class of bug (const/type width mismatch in the arithmetic),
  fixed, and only then gates run.
- **Verified every boot, both toolchains**: the good carrier's load
  walks its six mapped facts, its entry point runs in ring 3 and
  writes a magic only its own code could have written
  (0xC0DE_DA7A_C0DE_DA7A at 0x5500_0010_0000, the digit-swap class
  named by earlier demo artifacts), then `int $0x8E` (a vector
  dedicated to `pe_exception_demo`, IDT-registered beside the
  existing demo vectors); the MIS and VER carriers are each REFUSED at
  load with their typed reason (the two negative-control windows and
  the composed deltas are pinned in `tools/verify_prog_layout.sh` like
  every other patcher family). The demo then chains into the
  resource-directory increment below, keeping the single linear demo
  chain. Gate + boot PASS on both toolchains (the 26.04 round card
  above recorded 294 `-- OK` fact lines including these).

## PE increment (2026-10-24, second): the startup IMAGE_DIRECTORY_ENTRY_RESOURCE walk -- a real two-level tree, ring-3 leaf consumption, three typed refusals

- **Kernel side** (`experimental::pe`, `pe_resource_demo`): the loader
  now walks `DataDirectory[2]` when declared. The walk: one startup
  directory, then exactly one NAMED entry and exactly one SUBDIRECTORY
  entry are followed (the facts struct carries the walk's state
  explicitly; a mutable NameBuf holds names read out of the image,
  capped at 64 bytes with the bytes HTML/terminal-escape-safe by
  regulation -- the transcript must be uncorruptible by a hostile
  name). The subdirectory resolves exactly one ID entry, then exactly
  one leaf. Per-directory caps: at most 16 entries named or ID per
  directory (the walk refuses a 17-slot array: the cap is proven
  EXCLUSIVE from both sides -- exactly 16 accepted, 17 refused) and
  depth at most 3. Each leaf's data (a null-terminated string) must
  land entirely inside the image footprint (a leaf whose bytes run
  past the image is `MalformedResourceDirectory`, not an unbounded
  read). Every refusal typed.
- **Client side**: `userprog/prog_pe_res.s` + `tools/patch_pe_res.py`
  -- FOUR carriers of the ONE assembly: `GOOD.exe`'s tree is
  one named entry -> one subdirectory -> one ID entry -> one 8-byte
  leaf string; the leaf size was deliberately kept within the composed
  block's footprint so even the good carrier never reads outside what
  it itself declares; `RBAD.exe` carries the leaf-Size-past-footprint
  property, `RNAME.exe` an empty length-0 name string, `RMANY.exe` a
  17-ID over-cap array. The composition lives where the pdata one does
  (`.idata`'s in-block padding, `dir[2]` pointed at it, VirtualSize
  grown, on-disk re-parse proves the bytes).
- **Verified every boot, both toolchains**: the good carrier's load
  walks the four tree facts, its entry point runs in ring 3, writes
  its magic, then `int $0x8F` (the vector dedicated to
  `pe_resource_demo`), whose handler reports the ring-3 result and
  chains into `experimental::isolation_demo` -- the demo chain stays
  one line: pe_import -> pe_exception -> pe_resource -> isolation;
  the three negative controls each refuse at load with their typed
  reason (windows + pins in the layout gate like every other family).
  Full build, layout gate, BIOS gate and UEFI gate all PASS on the
  record date.

## Phase 10 increment (2026-10-25): the SecureBoot variable's protocol proven end-to-end BEFORE it is trusted -- a stub-written variable-services round trip with every answer pinned

- **Why**: `read_secure_boot` (the stub's SecureBoot read, UEFI
  variable services via the runtime-services table) until now rode on
  an UNVERIFIED contract: the stub had no way to check that the
  firmware's GetVariable honors the spec answers it assumes (query
  pass = `EFI_BUFFER_TOO_SMALL` with the TRUE size; data pass fills
  the caller's buffer with exactly what was stored). A firmware that
  lies about one variable's protocol lies about SecureBoot. The
  checkable-at-every-boot proof is a scratch variable the stub writes
  ITSELF and knows the bytes of, exercised set/get/query/delete
  against the LIVE runtime services, dying the boot with the exact
  failed step if any pin fails.
- **What the round trip pins** (every step's answer is the spec's):
  step0 pre-use GET must answer `EFI_NOT_FOUND` (a pre-existing
  scratch means two boots interleaved); step1 SET of a 4-byte payload
  (`5aa53cc3`) must be `EFI_SUCCESS`; step2 GET must return exactly
  those 4 bytes at exactly size 4; step3 a too-small query (size 2
  offered) must answer `EFI_BUFFER_TOO_SMALL` naming the TRUE size 4
  -- this is precisely the pass `read_secure_boot` relies on to learn
  SecureBoot's fixed size of 1; step4 delete-then-get must give
  `EFI_NOT_FOUND` again. The scratch variable is deliberately
  VOLATILE (`BOOTSERVICE_ACCESS` only -- no NON_VOLATILE bit) so the
  check can never write firmware NVRAM.
- **Two real bugs found and fixed in the act** (the incremental kind
  that repays building the check):
  1. The stub's hand-rolled `EFI_RUNTIME_SERVICES` struct was MISSING
     the spec's SetWakeupTime pointer slot entirely (13 slots where
     the spec fixes 14) and typed `set_variable` as an untyped slot
     width -- every pointer from that break on landed in the WRONG
     table slot. Fixed: `set_variable` typed at the spec signature
     (name, guid, attributes u32, data_size, data), `set_wakeup_time`
     slot inserted after `get_wakeup_time`, and the compile-time size
     assertion corrected to the spec's 24 + 14*8 = 136 bytes so drift
     cannot re-enter silently.
  2. The first SetVariable attempts answered `EFI_INVALID_PARAMETER`
     from BOTH shipped OVMF builds -- while a zero-size delete of the
     same (nonexistent) variable answered exactly `EFI_NOT_FOUND` and
     GetVariable stayed spec-perfect through the same call shape,
     proving the ABI/mechanics correct and the refusal in the
     firmware's namespace policy: the scratch variable had been named
     under `gEfiGlobalVariableGuid`, whose namespace the spec reserves
     for spec-defined names (the edk2 driver-writers guidance says
     firmware code must never store anything there using it). Fixed
     by giving the stub its OWN vendor GUID for the scratch variable
     (`VARCHK_VENDOR_GUID`, a fixed constant documented in `efi.rs`);
     with it every step passes on real OVMF, securely under the
     shipped `OVMF_CODE_4M.fd` and the `.ms` build alike.
- **Wired where it belongs**: `verify_variable_services` runs in
  `efi_main` immediately after the SecureBoot fact is printed (before
  allocations), so a lying firmware never reaches a page allocation,
  let alone `ExitBootServices`. The stub deletes the scratch before
  EBS in every success path (step4 proves deletion works).
- **Gate mechanics**: `tools/qemu_uefi_boot_test.sh` now ALSO captures
  OVMF's DXE debugcon output every run
  (`target/qemu-uefi-test-ovmf.log`) so a refused firmware call names
  its own reason on inspection, not only a status code. Recorded
  transcript (the shipped firmware, `SecureBoot=absent` -- ADR-0014
  keeps SB-off boot legal, the fact is a fact not policy):

```
nyrqis ubld: stub alive under UEFI; SecureBoot=absent
  varchk: step0 get = EFI_NOT_FOUND, correct (nothing written yet)
  varchk: step1 set (4 bytes) -- EFI_SUCCESS
  varchk: step2 get -- the 4 stored bytes, exact (5aa53cc3)
  varchk: step3 too-small query -- EFI_BUFFER_TOO_SMALL with the true size 4
  varchk: step4 delete then get -- EFI_NOT_FOUND, really gone
  varchk: variable-services roundtrip OK -- the SecureBoot read's protocol is firmware-honest
```

  then the full demo chain reaches its final banner and the UEFI gate
  PASSES with the round trip in it.

### The parity re-pass across the new increments (2026-10-25): both toolchains rebuilt from-scratch, gates keyed, both boots PASS

After the exception-table + resource-tree + variable-services
increments landed, the whole pipeline was re-proven on BOTH toolchain
lines exactly as the 26.04 card prescribes: every composed PE artifact
removed first (`rm userprog/userprog_pe*.exe *.dll` -- the patchers
refuse re-composition by design), then a from-scratch rebuild with
2.46's as/ld/objdump first in PATH (its unpacked root under
`target/toolchain-2.46/`, libbfd/libsframe from the same root on
LD_LIBRARY_PATH), the layout gate keyed on the ld in PATH, and BOTH
boot gates against the 2.46-linked kernel -- build exit 0, gate exit
0, BIOS PASS (251 `-- OK` fact lines), UEFI PASS with the
variable-services roundtrip in it (250 `-- OK` lines). The baseline
was then re-proven the SAME WAY on the host default 2.42/2.43: build
exit 0, gate exit 0, BIOS PASS (251 `-- OK` lines), UEFI PASS (250
`-- OK` lines). Both toolchains carry the same kernel-side behavior
end-to-end (the full demo chain now including the pdata/res walks,
the three+four carrier refusals, and the stub's varchk), differing
only in the catalogued, keyed layout literals.

## Phase 10 increment (2026-10-25, second): the ADR-0014 chain lands its FIRST link -- the stub verifies the embedded kernel image's SHA-256 against the link-time digest, every boot, and refuses mismatches by name

- **What this is, honestly scoped.** ADR-0014's accepted chain is
  shim-equivalent + Nyrqis key + optional user MOK. This increment
  lands the chain's first link: the stub (the shim-equivalent first
  stage) now hashes EVERY byte of the embedded kernel image with a
  real SHA-256 and compares against the digest `build.sh` recorded at
  link time, BEFORE a single kernel byte is copied or one instruction
  of the kernel runs. It is an INTEGRITY pin, not yet the full
  authenticated chain: no PK/KEK/db carriers, no Authenticode
  signature verification, no enrollment UI -- those are the next
  Phase 10 increments; what this buys right now is that a corrupted
  or substituted payload CANNOT reach `uefi_entry` silently.
- **The hash is hand-rolled and STILL not trusted unreviewed**
  (`uefi/src/sha256.rs`, FIPS 180-4 by the letter, no dependencies):
  the known-answer vectors (empty input, "abc") are computed at every
  boot BEFORE the check, and a wrong vector halts the boot -- a
  mis-implemented hash cannot mis-declare a kernel honest.
- **The expected digest is a build artifact, not source**:
  `build.sh` writes `uefi/src/kernel.sha256` (exactly 64 lowercase hex
  chars -- `tr -d '\n'` strips cut's newline; the first cut shipped 65
  bytes and the compile itself refused `include_bytes!` into a fixed
  `[u8; 64]`, which is the right crash) immediately after
  `target/kernel.elf` links, so the digest and the image are always
  the same build's. The file is gitignored (same policy as
  `target/`), and cargo tracks it, so the stub recompiles whenever
  the kernel does.
- **The refusal path is proven live at every boot**: alongside the
  match, a one-bit-flipped COPY of the image is hashed and must
  produce a DIFFERENT digest -- if it hashed equal, the comparison
  above would not be a check at all, and the boot dies naming that.
  Both digests (computed and recorded) travel on the console:

```
nyrqis ubld: sha256 known-answer vectors OK
nyrqis ubld: kernel image sha256 computed = 560d51e4699337b452aa45b117bc54d6b0d7b2f71a1b2946a250e9ee241e217f
nyrqis ubld: kernel image sha256 recorded = 560d51e4699337b452aa45b117bc54d6b0d7b2f71a1b2946a250e9ee241e217f
nyrqis ubld: integrity control OK -- a one-bit-flipped copy hashes differently, the refusal path is alive
```

  then the full chain reaches its final banner: the UEFI gate PASSES
  with the Stage-2 check in it (recorded 2026-10-25; the check sits
  between the variable-services round trip and `elf::parse`, so a
  lying-protocol firmware AND a lying payload are both refused before
  any allocation). The BIOS path is structurally unaffected: GRUB
  loads the kernel directly and the stub never runs there.

## Phase 10 increment (2026-10-26): the ADR-0014 chain lands its SECOND link -- the stub verifies an RSA-2048 PKCS#1 v1.5 carrier signature over the SAME bytes it boots, every boot, refusal named or halting

- **What this is, honestly scoped.** The accepted chain is
  shim-equivalent + Nyrqis key + optional user MOK. Link one (the
  2026-10-25 increment) was the INTEGRITY pin. This increment is the
  AUTHENTICATED half of the shim-equivalent stage: the stub now
  include_bytes!es TWO more artifacts that `build.sh` writes right
  after the kernel links -- `uefi/src/trusted-root.der` (a self-signed
  X.509 anchor certificate) and `uefi/src/carrier.der` (a DETACHED,
  attrless PKCS#7/CMS SignedData over the full kernel image) -- and
  between the sha256 pin and `elf::parse` it walks the carrier and
  verifies the RSA-2048 PKCS#1 v1.5 signature over
  sha256(kernel bytes). What this buys now: a kernel image whose
  bytes do not verify against the embedded anchor CANNOT reach
  `uefi_entry` silently, sha256-identical to a build artifact or not.
  Still open, named: no chain-of-trust beyond one self-signed dev
  anchor (no PK/KEK/db carriers), no enrollment UI, no expiry or
  revocation reading (the stub has no RTC and pins the anchor
  byte-for-byte instead), no Authenticode/PE signature -- those are
  the next Phase 10 increments.
- **The stub side is hand-rolled and STILL not trusted unreviewed**
  (`uefi/src/der.rs`, `uefi/src/rsa.rs`, `uefi/src/trust.rs`):
  RFC-pinned walk at every step -- X.690 TLV with definite lengths
  only (`der.rs`, indefinite + >4-byte lengths refused), a fixed
  2048-bit bignum with an exact 65-limb accumulator and e=65537
  public-key modpow (`rsa.rs`), and the CMS one-carrier walk
  (`trust.rs`): ContentInfo -> SignedData -> digestAlgorithms SHA-256
  -> encapContentInfo WITHOUT eContent (a carrier carrying its own
  payload is refused -- the stub verifies ITS OWN copy of the bytes,
  not a second copy shipped for it) -> certificates [0] -> one
  SignerInfo whose sid must byte-equal the anchor's issuer+serial,
  whose signatureAlgorithm must be rsaEncryption or
  sha256WithRSAEncryption, and whose signedAttrs being present is a
  refusal (`-noattr` is the carrier contract). Every refusal is a
  separate '&static str' naming the exact step; the boot dies on all
  of them.
- **The trust anchor is byte-equality, not a chain walk.** The
  carrier's embedded signer certificate must BYTE-EQUAL the embedded
  anchor DER, and the anchor itself is parsed only enough to extract
  its RSA SPKI (modulus + exponent) and its issuer/serial. The
  anchor's own self-signature is deliberately NOT verified: a
  signature whose key would be what the signature proves can add
  nothing over byte equality, and the honest statement of what this
  stage trusts is "the developer's key, in the stub, as source".
  Enrollment is the next increment, not a hidden chain walk here.
- **The signer side is `tools/make_carrier.sh`, called by
  `build.sh` right after the kernel link and the sha256 record.** It
  generates a dev RSA-2048 key (e=65537) and self-signed anchor
  certificate ONCE into `uefi/tools/trust/` (gitignored; regenerated
  only when absent, and rebuilt whenever the cert disagrees with the
  key on disk), converts the certificate to DER, then `openssl cms
  -sign -binary -md sha256 -noattr -outform DER` the kernel image --
  DETACHED, so the carrier is ~1.4 kB, not a second 7 MB copy -- and
  SELF-VERIFIES its own output with `openssl cms -verify -noverify`
  before returning. The signature is deterministic for key+content
  (no signedAttrs, no randomness in PKCS#1 v1.5), so the carrier
  bytes change exactly when the kernel image does: cargo recompiles
  the stub on a kernel change and only then.
- **Both halves are pinned at every boot by the same live-control
  pattern as the sha256 check**: the positive path verifies over the
  REAL bytes, then a one-bit-flipped COPY of the content must arrive
  at a named refusal ("rsa: DigestInfo digest differs") -- if a
  flipped copy verified, the comparison above would not be a check,
  and the boot dies naming that. The transcript that ships this:

```
nyrqis ubld: sha256 known-answer vectors OK
nyrqis ubld: kernel image sha256 computed = ... (unchanged link)
nyrqis ubld: carrier verify: pinning the signer certificate against the embedded anchor, then walking the detached carrier
nyrqis ubld: carrier verify OK -- RSA-2048 PKCS#1 v1.5 signature over the SAME bytes about to be loaded
nyrqis ubld: carrier refusal-path control OK -- a one-bit-flipped copy verifies to a named refusal, the trust path is alive
```

- **The carrier blob missing is a refusal with a name, not a silent
  pass.** `include_bytes!` on an absent file fails the compile (the
  stub cannot build without the two DERs, by construction, the same
  property kernel.sha256 has); a file written empty is refused at
  the first named check; everything else is refused by one of the
  walk's named steps. The refusal-path walking host-side lives in
  `uefi/tests/trust_selfcheck.rs` (rustc, not cargo, no UEFI
  needed): run against the REAL carrier bytes it proves the
  positive path plus four refusal paths (flipped content, flipped
  anchor, flipped carrier, truncated carrier) in one command, which
  is how the walk was pinned BEFORE the UEFI gate saw it:

```
-- OK 1: carrier verifies over the 7269152-byte kernel image
-- OK 2: flipped content refused at the digest comparison: rsa: DigestInfo digest differs from the message digest
-- OK 3: flipped anchor refused (256 bytes, pin at offset 842)
-- OK 4: flipped carrier refused: carrier: the signer certificate's DER does not byte-equal the embedded trusted anchor
-- OK 5: truncated carrier refused
PASS: trust walk proves positive + 4 refusal paths on the real carrier bytes
```

- **The UEFI gate ran and PASSED with the carrier check in it**
  (recorded 2026-10-26): `tools/qemu_uefi_boot_test.sh` rebuilt
  everything from source in the same order as always, the stub was
  built against the fresh kernel image, and the FULL demo chain ran
  to its `BootPhase::Ready` exit-code contract under OVMF -- boot
  exit 33, kernel transcript complete, with the carrier lines above
  sitting between the sha256 pin and the kernel's own boot banner.
  The BIOS path is structurally unaffected: GRUB loads the kernel
  directly and the stub never runs there.

## Phase 10 increment (2026-10-26, second): the ADR-0014 chain lands its THIRD link -- the carrier is signed by a LEAF over the root anchor (a real two-certificate chain), five generated negative carriers must refuse for their NAMED reasons, and the boot side attempts the chain's firmware-facing half (a db enrollment) honestly, recording what the firmware wills

- **The certificate chain is real now.** `tools/make_carrier.sh`
  reuses the self-signed root (the previous increment's anchor),
  generates a fresh LEAF RSA-2048 key + leaf certificate signed BY
  the root (keyCertSign on the root, digitalSignature on the leaf),
  and signs the carrier with the leaf -- `openssl cms -sign -binary
  -md sha256 -noattr -outform DER`, detached, exactly as before. The
  stub unchanged in its contract: pin the embedded signer
  certificate, then verify the signature. What changed UNDER the
  pin is that the embedded signer certificate is no longer the
  anchor itself: it is the leaf, and the leaf must VERIFY under the
  anchor's key (issuer+serial chain, keyUsage carried correctly:
  the BIT STRING's named bits count from the MSB -- root
  keyCertSign is `03 02 01 06`, leaf digitalSignature is `03 02 07
  80`, confirmed against `openssl asn1parse` before pinning the
  masks, because an LSB-first guess parses straight past). The
  host selfcheck and the boot transcript both exercise the chain.
- **The gate for a substituted signer got harder for free.** The
  previous design refused ANY flip of the anchor bytes by an anchor
  byte-equality pin; the chain design does not verify the anchor's
  own self-signature (a signature proves the key made it -- the key
  is WHY the chain starts at the anchor, so that statement is
  inert). What that leaves protected is exactly what matters: a
  corrupted anchor KEY is refused (the chain's root verification
  now fails), which the UEFI gate's negative-anchor control proves
  by name (`carrier: the embedded signer certificate is not the
  anchor and does not verify under the anchor's key (no chain to
  the root)`). The old harness check that pinned ANY anchor-byte
  flip therefore no longer applies; it was REWRITTEN to flip inside
  the parsed modulus and expect the named chain refusal.
- **Five negative carriers are generated and must refuse, each for
  its own named reason.** `tools/patch_carrier_negatives.py` is a
  DER-aware byte-surgery tool (TLV lengths walked per X.690 so a
  corruption that changes sizes knows which ones to fix); it emits
  the digest, anchor, sid, signedAttrs, attached-eContent variants.
  The stub's boot path (`main.rs`) embeds all five via
  `include_bytes!` (compile fails when any is missing -- the stub
  cannot be built without proof of its refusal set) and MUST refuse
  every one of them by a substring of the walk's own refusal text;
  a negative refusing for the WRONG reason (a parse truncation that
  skipped the real check) also dies by name. `uefi/tests/
  trust_selfcheck.rs` grew the same five negative controls host-side
  (`tools/verify_carrier.sh` runs it as part of every `build.sh`,
  and also exercises the five-carrier refusal pins against the
  files on disk).
- **Enrollment (the chain's firmware-facing half) is attempted
  every boot and honestly recorded.** `boot.rs`
  `enroll_trusted_root_in_db`: gate on the PK variable (a PK SET
  means authenticated-var mode and a KeK-signed AUTH package --
  skipped with a named fact, the stub fabricates none); the
  firmware's own GetTime datums the write (an implausible clock is
  a named, non-fatal skip); the descriptor is the UEFI 2.10
  EFI_VARIABLE_AUTHENTICATION_2 envelope built to edk2's own
  EnrollDefaultKeys application's EXACT construction (24-byte
  empty-pkcs7 win-certificate, live date+time, all sub-second/zone
  fields zeroed) and the write carries
  NV|BS|RT|EFI_VARIABLE_TIME_BASED_AUTHENTICATED_WRITE_ACCESS; a
  SetVariable replaces -- an existing db is read back and APPENDED
  to, never clobbered; the write is claimed only after read-back
  pinning. Everything short-stepped -- including refusal -- is a
  named fact, not a hazard: the stub's own chain gate is what
  enforces trust, not the firmware store.
- **A real firmware-side refusal was bisected to a stub packet bug,
  and to an image capability fact.** The first SetVariable came back
  EFI_INVALID_PARAMETER; three differently-routed writes plus
  `GetVariable(SecureBoot/SetupMode/VendorKeys)` queries (LEFT DISABLED
  in `boot.rs` behind `DB_ENROLL_PROBE=false`, carrying what they
  proved as the reason on their face) split the refusal into two
  answers. FIRST, a real defect: `CERT_TYPE_PKCS7_GUID` in `efi.rs`
  had a correct first DWORD and a wrong rest-of-GUID -- edk2 checks
  the auth2 descriptor's win-certificate CertType GUID before
  almost anything else and refuses anything else; the constant now
  matches MdePkg `ImageAuthentication.h` byte for byte. SECOND, a
  capability fact: the plain non-secboot OVMF image bundled in
  `uefi/tools/` installs NO authenticated-variable service at all
  (`SecureBoot`, `SetupMode`, `VendorKeys` all `EFI_NOT_FOUND`;
  every TBA write, to any name, is `EFI_INVALID_PARAMETER`), while
  the `.ms` sibling image compiles the enforcement in but then
  refuses to even LOAD an unsigned stub (BdsDxe "Access Denied") --
  so no bundled image can both run the stub and host the service.
  (2026-10-27 update: that is only a wall for UNAPPROVED images -- the
  2026-10-27 card below enrolls the carrier chain itself and
  leaf-signs the stub, so the enforcing image now both RUNS the stub
  and hosts the service; the stub's OWN db write is still refused
  there, and that gate's transcript records it as the named fact.)
  The enrollment records the refusal as the known, named fact it
  now is, and the boot proceeds -- which was always its contract.
- **The wandering 'timeouts' are explained, and the real cause is
  fixed at the source instead of papered over.** The archive task
  cited "hung"; the monitor socket sampled registers during a
  reproducer: the CPU was ALIVE at moving addresses -- it was
  still computing. The original bit-serial modpow (2048 doubling
  steps, ~2*10^8 u32 limb operations per verification) under TCG
  cost tens of seconds per call, so the full five-negative harness
  outlived a 120 s cap. Rather than only raising the cap, the
  cause was rewritten away: `rsa.rs` now runs public-key modpow
  via Montgomery multiplication (schoolbook 128-limb product +
  textbook per-limb REDC -- one cancellation multiplier per limb,
  caught by probe before any carrier saw it -- R^2 mod n derived
  by 2048 bounded double-and-subtract steps, ~10^6 u32 operations
  per verification). The full host selfcheck (good carrier + five
  negatives) now proves in 0.86 s wall time, and the UEFI gate in
  TCG boots in two minutes overall, dominated by the kernel's own
  selftest suite -- not by the trust walk. The 480 s cap in
  `tools/qemu_uefi_boot_test.sh` stays as the deadlock detector
  with its comment telling THIS story, not the old one.
  Two real shape bugs fell out of the rewrite, catalogued because
  they are classic Montgomery traps: a single global reduction
  multiplier makes T + m*n divisible by 2^32 but NOT by R (toy
  probe returned the wrong value with the trap's own words to
  prove it), and entering the domain via mont_mul(base, R mod n)
  is a numerical no-op (base*R*R^{-1} = base) -- the entry factor
  must be R^2 mod n. Both were caught by the same probe discipline
  (mirror the algorithm in Python against a true pow() before
  compiling), not by burning gate runs.
- **Gate transcripts (2026-10-26, this bundle image).** Boot 1
  (fresh template store) and boot 2 (same store, persisted) both
  completed: chain verify OK, all five negatives refused for their
  named reasons, kernel reached `BootPhase::Ready`, host exit 33.
  The VARS store persist-check found the store free of stub
  variables (the varchk's round-trip variable is deleted by the
  stub itself in step 4, the db write is refused by the image as
  documented, and the negative probes are disabled) -- recorded as
  the physical evidence, not asserted from code.
## Phase 10 increment (2026-10-27): the ADR-0014 chain's firmware-facing half GATES both directions on the enforcing .ms OVMF image -- the leaf-signed stub accepted under SecureBoot=1, the unsigned and the one-byte-tampered signed stubs refused BY THE FIRMWARE

- **What this is, honestly scoped.** The chain's firmware-facing
  half -- the piece the third card could only attempt honestly, when
  the plain image hosted no authenticated-variable service and the
  `.ms` sibling refused an unsigned stub outright. On the enforcing
  `.ms` image the carrier chain's OWN dev key hierarchy becomes the
  firmware's, and the stub is Authenticode-signed with the leaf key:
  the FIRMWARE, not the stub, is the gate deciding which images may
  load, while the stub's own carrier walk still proves the kernel
  image inside the image the firmware already allowed.
- **The store is composed offline from the PLAIN variable-store
  template, not the `.ms` one** (`virt-fw-vars`, pip name
  `virt-firmware`): the carrier ROOT as PK and KEK, root + leaf
  certificates in db, `SecureBootEnable` ON. The plain template is
  deliberate: `virt-fw-vars` only ADDs entries to a store it reads,
  never removes them, so a `.ms`-template copy would keep the
  template's Microsoft authorities in KEK/db and the composed store
  would not be the one this gate claims to prove against (extracting
  the certs from the first composition showed exactly that merge).
  The signer certificate is enrolled verbatim (the leaf), so
  `AuthenticodeVerify` binds the stub's signer on sight -- no
  cross-signing ceremony needed for the dev chain.
- **Three boots, one script, all three assertions physical.**
  `tools/qemu_uefi_ms_secboot_test.sh` runs the same kernel+stub
  build as the non-ms gate, then: positive -- the signed stub loads
  under enforcement with `SecureBoot=1` and PK PRESENT (not
  SetupMode), walks its carrier chain the usual way, and the kernel
  reaches Ready (host exit 33, the same contract); negative 1 -- the
  UNSIGNED stub against the same store: the boot manager's "Access
  Denied", and no stub line ever prints (the refused VM idles, caught
  by the run's timeout); negative 2 -- the SAME signed image with ONE
  flipped byte inside its first hash-covered section (the probe
  parses the PE's own section table and flips one bit half-way
  through the data, so no header or certificate byte changes): also
  refused the same way -- enforcement covers signed-but-modified
  images, not only unsigned ones. The negative verdicts are asserted
  by ABSENCE (`Access Denied` AND no `stub alive` line), because a
  refused boot does not exit -- it idles in the boot-manager loop
  until the timeout, which is the verdict, not a hang.
- **Two enrollment traps cost a debugging cycle and are catalogued.**
  First, `virt-fw-vars` with `-i` alone only PLANS its changes -- the
  store on disk keeps the template's Microsoft keys, and the firmware
  then refuses even a correctly-signed image, which sent the first
  suspect list to the wrong place (the signature); the script now
  passes `--inplace` explicitly, and asserts the enrollment it meant
  to write by printing the composed store's shape. Second, the stub
  is signed with the LEAF (not the root) so the db's verbatim leaf
  entry is the one AuthenticodeVerify binds; a root-signed stub
  would have introduced a needless second trust question.

## Phase 10 increment (2026-10-28): the ADR-0014 chain lands its expiry + revocation reading -- the walk's clock is the FIRMWARE'S OWN GetTime (refusal-gated), both chain certificates' validity windows gate before any content digest, and the firmware's dbx revokes a chain the walk just verified, four ways, by name

- **What this is, honestly scoped.** The third open card of the
  ADR-0014 1.1.0 scope-measure: the chain's TIME and REVOCATION
  gates. The carrier walk already verified the signature and the
  one-hop chain; this card adds the two things a signature alone
  cannot say: that the keys are being used INSIDE their validity
  windows (compared against the firmware's own RTC reading, not a
  stub-side constant), and that the chain is not sitting in the
  firmware's forbidden-signature database (`dbx`). Both gates sit
  between carrier verification and uefi_entry: a chain that WAS
  valid and unrevoked at its last boot but is expired or revoked now
  refuses, by name, at its own step.
- **The clock is a firmware-owned datum, every boot.**
  `boot.rs::firmware_time_or_die` reads runtime-services GetTime and
  refuses (named) both an unreadable clock and an implausible
  reading (year outside 2000..2999, month/day out of range) -- the
  windows only gate honestly if the datum they are compared against
  is a real clock, so a dead or lying RTC halts instead of quietly
  passing everything. `trust.rs::Rfc5280Time` holds exactly the six
  fields RFC 5280 validity carries at second precision; comparison
  is plain field-tuple ordering (both sides civil-calendar), so
  there is no calendar arithmetic anywhere to get wrong.
- **Validity parsing is per-slot named.** `parse_rfc5280_time`
  accepts UTCTime (`YYMMDDHHMMSSZ`, through 2049) and GeneralizedTime
  (`YYYYMMDDHHMMSSZ`, beyond -- RFC 5280 4.1.2.5's encoding rule,
  exactly the two forms openssl emits, verified against its own
  output before the shape was pinned); every refusal names the slot
  it appeared in (`notBefore` / `notAfter`), the encoding problem
  (wrong width, non-digit, no Z, out-of-range calendar value, wrong
  tag), and refuses at the next-of-kin step -- a validity member the
  walk cannot read is a refusal, never a "skip and continue". The
  anchor's window gates too (`assert_valid_at(.., "anchor")`), and
  the LEAF's window gates BEFORE the content digest: the two window
  negative carriers pin the ORDER (an expired signer refuses at the
  window name, not later at or after a successful RSA).
- **`dbx.rs`: the revocation surface, sat behind the walk.** The
  `dbx` variable (EFI_IMAGE_SECURITY_DATABASE_GUID, UEFI 2.10 section
  32.4.1) is read whole through the same GetVariable contract as
  every other variable; the EFI_SIGNATURE_LIST walk enforces
  EFI_CERT_X509_GUID entries two ways -- whole-certificate byte
  equality against the signer or the anchor, and (after parsing the
  entry with the SAME certificate reader the chain used)
  issuer-Name + serialNumber byte equality, the pair X.509 treats as
  THE identity a signature binds, so the "re-key with the old
  serial" attack is revoked with it. Entries of any other CertType
  are counted and reported as skipped -- named facts, never
  silently enforced or silently dropped -- and structural breakage
  (a list header whose sizes disagree with the blob it lives in)
  refuses rather than truncates. Four refusal strings total, each
  naming which certificate matched and by which rule.
- **The generated negative set grew to keep the gate honest,
  every boot, both in-stub and host-side.** `make_carrier.sh` now
  emits two window carriers (real root + REAL LEAF KEY, so the
  chains are genuine and the window gate is the only thing between
  each and a load): expired at 1990..2020 (UTCTime) and
  not-yet-valid at 2400..2500 (GeneralizedTime), each issued over a
  fresh wiped throwaway CA database with explicit
  `-startdate/-enddate` so no today-relative default value rides
  into bytes the stub parses (an `openssl ca` shape lesson: without
  an explicit `-extensions` section the issued certificate came out
  v1 with NO extensions and the negatives refused at the keyUsage
  shape check -- caught because the wrong-name refusal died the
  build instead of the right one). And three dbx blobs: the leaf by
  byte, the anchor by byte, and a genuine re-key -- a certificate
  RE-ISSUED by the same root with THE SAME serial over a DIFFERENT
  key, where the serial is read straight out of the good carrier's
  SignerIdentifier (`patch_carrier_negatives.py print-sid-serial-hex`)
  and forced through the CA serial file (`openssl ca` in this
  OpenSSL has no `-serial` option; the serial file is the
  supported path). `main.rs` refuses at boot if any negative
  verifies, or is refused for a reason other than its own -- the
  same exclusivity the carrier set pinned.
- **Host harness grew with the walk.** `trust_selfcheck.rs` now
  takes the walk clock from the host's own UTC time (std-only
  civil conversion, printed for cross-checking against `date -u`;
  the firmware's GetTime plays this role at boot) and compiles the
  stub's `dbx.rs` + `efi.rs` in via `#[path]` like the other walk
  modules -- then proves, host-side: positive chain verify returns
  the `TrustedChain` the revocation gate consumes; all seven
  negative carriers refuse for their named reasons; all three dbx
  blobs refuse the just-verified chain for their named reasons; and
  a synthesized unknown-CertType entry carrying the NULL-signer
  bytes is SKIPPED (one skipped entry, zero refused), proving the
  walk enforces only what it walks. `verify_carrier.sh` passes the
  full seven-carrier + three-blob + marker set.
- **The gates.** Host: `tools/verify_carrier.sh` PASS (positive + 6
  controls + 7 negative refusals + 3 dbx refusals + skip). BIOS:
  `tools/qemu_boot_test.sh` PASS. Plain UEFI:
  `tools/qemu_uefi_boot_test.sh` PASS with the boot transcript
  showing the firmware clock read (`walk clock: firmware GetTime =
  2026-10-28 10:07:52`), all seven carrier negatives refused for
  their named reasons (the two window names at their slots), the
  `dbx: absent on this firmware (EFI_NOT_FOUND)` named fact the
  default store gives, and the three dbx control refusals. Enforcing
  `.ms` gate: `tools/qemu_uefi_ms_secboot_test.sh` PASS
  (SecureBoot=1, positive + both Access-Denied negatives), and its
  transcript shows the REAL dbx the composed store carries being
  walked: one entry of an unenforced type skipped by name, none
  revoking the chain -- the revocation reading ran against a store
  the FIRMWARE itself wrote, not only the generated controls.

**Same-day update: hash-typed dbx entries are enforced too.**
The scope note above named EFI_CERT_X509_SHA256_GUID entries as a
follow-up; they landed hours later, and the walk's domain now covers
both UEFI 2.10 32.4.1 entry forms the real-world Microsoft dbx
updates actually use:

- **`dbx.rs`: the hash list branch.** An X.509-SHA256 list's entries
  are owner GUID + one `EFI_CERT_X509_SHA256_SIGNATURE` each -- an
  `EFI_TIME RevocationDate` (16 bytes) plus the SHA-256 of the
  certificate's To-Be-Signed  content (32 bytes) -- so the walk hashes
  each chain certificate's TBS via the new `trust.rs cert_tbs()` (the
  whole TLV over an independently walked certificate, byte-identical
  to what `Cert::parse` stores) and compares. A matching hash
  refuses by name, and the standoff date is exactly the entry's
  RevocationDate read as six civil fields and required to be at or
  before the firmware clock the walk already fetched: a future-dated
  entry is NOT yet in force -- it stays a countable walk fact, never
  a refusal. `sig_size` that is not 16+48 refuses structurally.
- **The negative set grew to five.** `make_carrier.sh` emits two
  hash-typed blobs (`make-dbx-hashlist`, revoked 2020-01-01 so any
  boot clock after that date refuses): the leaf's TBS hash and the
  root's. `verify_carrier.sh` passes five dbx pairs, and the host
  harness gains a synthesized FUTURE-dated hash entry (revocation
  2400) over the real leaf's TBS hash, proving the date gate -- it
  is walked and counted, never refused. `main.rs` grew to five
  every-boot dbx controls.
- **Verified:** host `verify_carrier.sh` PASS (positive + 6
  controls + 7 carrier refusals + 5 dbx refusals + BOTH skip
  facts), and the plain UEFI gate's transcript shows all five dbx
  controls refusing on real firmware. (A find worth recording: the
  first Python TBS walker sliced the whole-TLV arithmetic wrong for
  long-form lengths -- 601 vs the stub reader's 608 bytes -- so the
  blob carried a hash over the WRONG bytes and the negative did NOT
  refuse; the host gate caught it the same way it catches dead
  refusal paths, and the walker now anchors the child at the outer
  SEQUENCE's content start.)

**Same-day update 2: the MOK enrollment reading (the chain's
OPTIONAL user surface) is honored.** This is the "other open tool"
above: the stub now READS shim's `MokList` variable (shim's own
vendor GUID 605dab50-e046-4300-abb6-3dd810dd8b23 -- the store shim's
MokManager writes for a user's enrolled keys), and the new
`mok.rs` parses it with the SAME signature-list rules dbx.rs
enforces (named with MokList prefixes; structural breakage refuses);
an `MokList` entry acts as an ADDITIONAL chain anchor through a new
`trust.rs verify_mok_anchored_chain` entry point -- verbatim the
embedded-anchor walk, minus the CA shape assertion (a user's MOK
can be a leaf signer; a MOK-anchored carrier signed DIRECTLY by the
enrolled key is the walk's own signer==anchor shape) and keeping
the entry's own validity-window gate under its own refusal name.
Everything downstream is free: the windows gate, the content
digest RSA, and the dbx revocation walk take the MOK chain's
TrustedChain as-is.

- **Enrollment itself stays out.** MokManager's UI is what writes
  MokList; the stub honors what is there, names what is not (the
  absent store is the same named-fact discipline as `dbx:`
  `EFI_NOT_FOUND`), and NEVER silently extends: the transcript
  reports covered/uncovered per entry, a fact, not a refusal.
- **The control material is generated.** `make_carrier.sh` builds a
  second INDEPENDENT chain (MOK CA -> MOK leaf, v3 critical
  digitalSignature) plus an MOK carrier over the SAME kernel bytes,
  a MokList blob carrying the MOK CA (the enrollment shape), a
  STRANGER MokList blob (the REAL embedded root on the inside), and
  `dbx-neg-mok-hash.der` (the MOK leaf's TBS hash). Four controls
  run every boot, both host-side and in-stub: M1, the MOK carrier
  verifies under the generated MokList entry (anchoring works as
  enrolled); M2, it refuses under the EMBEDDED anchor (MOK trust
  does NOT ride the built-in pin); M3, it refuses under the
  stranger MokList blob (an unrelated MokList covers nothing); M4,
  the MOK leaf's hash blob refuses the MOK chain (the revocation
  gate covers MOK-anchored boots too).
- **Verified:** host `verify_carrier.sh` PASS with all four MOK
  controls named, and the plain UEFI gate transcript fires all of
  them on real firmware (`MokList: absent on this firmware
  (EFI_NOT_FOUND) -- no shim user keys (named fact)` on the default
  OVMF store, then the three refusal facts + the anchoring fact).

## Phase 10 increment (2026-10-29): real keys for ADR-0014 (ADR-0028) -- and the enforcing .ms gate composes a REAL shim MokList whose two facts the positive leg now requires

- **ADR-0028 is the real-key decision record, written after
  verifying against the code, not against intent.**
  `docs/reference/adr/ADR-0028-real-keys-and-moklist-content.md`:
  the walk is ONE-HOP (the leaf's TBS hash verified under the pinned
  anchor's SPKI), so the real-key hierarchy models exactly that --
  offline root, leaf per signing host -- and a two-tier tree would
  be a DIFFERENT ADR with different walk code, not a stronger
  variant of this one. The MokList content decision: a user
  enrollment carries the LEAF SIGNER, never the root anchor
  (enrolling the root would grant every future leaf it ever issues,
  collapsing the point of having a hierarchy). Revocation stays the
  dbx TBS-hash path the walk already enforces; leaves rotate freely
  (the anchor pins the issuer); production private keys live
  offline, never in the repo or on the build host.
- **The shim vendor GUID was verified against shim's actual
  source,** not remembered: rhboot/shim's lib/guid.c defines
  SHIM_LOCK_GUID = 605dab50-e046-4300-abb6-3dd810dd8b23 -- exactly
  the value the stub's efi.rs already pinned, and the same value
  virt-fw-vars' Shim GUID maps to. The `mok.rs` comment now names it
  correctly (shim reads every MokListRT/MokNew/MokAuth state
  variable from this GUID). Memory had it WRONG (wanted 8b71);
  checking the source settled it and CONFIRMED the stub was
  already right -- the read surface is shim-source-faithful.
- **The enforcing .ms gate now composes a real MokList too.**
  `tools/qemu_uefi_ms_secboot_test.sh` adds
  `--add-mok "$SHIM" "$LEAF_CERT"` under the verified
  SHIM_LOCK_GUID (virt-fw-vars' own `--add-mok` writes the
  `MokList` name, NV+BS attributes, Shim GUID -- checked in its
  source before use), holding the LEAF cert per ADR-0028's content
  decision. The composed-store print now lists MokList (this run:
  932 bytes) so the variable is INSPECTED, not assumed.
- **The gate's POSITIVE leg gained two required markers,**
  `MokList: walked` and `ALSO covers the just-verified chain`: the
  stub must actually READ the variable on real enforcing firmware,
  and the enrolled leaf must EXTEND the embedded anchor -- the
  walk's own signer==anchor shape (legal per ADR-0028), meaning the
  MOK trust path provably took an entry in rather than merely
  parsing one. A dead parse path fails the gate instead of silently
  shrinking to the old marker set.
- **And the new marker caught a REAL bug on its first fire.** The
  gate's first run failed exactly this requirement: with a real
  932-byte MokList in the composed store, the stub booted Ready but
  printed `MokList: walked 0 X.509 entries; skipped 1 entries of
  types this walk does not enforce` -- the REAL firmware-served
  signature list's X.509 entry was classified as "other". On-flash
  bytes were perfect UEFI 32.4.1 (three source checks against
  rhboot/shim, edk2's ImageAuthentication.h, and efivar/virt-fw-vars
  agree on the canonical GUID); the bug was OURS: `efi.rs`
  `CERT_X509_GUID` encoded time_mid as `0xe494` (a swapped u16 --
  its LE bytes are (94, e4) where the spec-wire bytes are (e4, 94)),
  and `CERT_X509_SHA256_GUID` was not the spec GUID at all
  (44689f75-... against the canonical 3bd2a492-96c0-4079-b420-
  fcf98ef103ed). Both host-side blob generators
  (`patch_carrier_negatives.py`) carried the SAME wrong bytes, so
  every generated control and every dbx negative agreed with the
  stub -- self-consistent, self-validating, and wrong against the
  outside world; only REAL firmware bytes could expose it, and it
  took the MOK surface being exercised against a REAL store for the
  check to fire. Both constants and both generator tables are now
  canonical (serialized spec bytes pinned in a comment at each
  definition), blobs were regenerated, and all gates re-ran green.
- **Verified:** TWO passes, recorded as run.
  PASS 1 (pre-fix): host `verify_carrier.sh` all 18 named OKs, plain
  gate PASS, `.ms` gate FAILED exactly on the new positive-leg
  markers (marker_ok=0; both Access-Denied refusals correct) --
  root-caused to the GUID byte order above, not papered over.
  PASS 2 (post-fix): host harness all 18 named OKs again with the
  regenerated canonical-GUID blobs, plain UEFI gate PASS, `.ms`
  secboot gate PASS on the full three-leg transcript (PK/KEK/db/
  dbx/MokList/SecureBootEnable composed store, positive boots Ready
  WITH both MokList markers fired on the real firmware-served
  variable, both Access-Denied negatives refused by the firmware).
  `mkdocs build --strict` passes with ADR-0028 registered in BOTH
  ADR indexes (NPC-005 v1.26.0 + adr/README row), and the
  doc-numbering collision this record replaces was caught before
  commit: the first draft of the ADR file carried the already-taken
  number ADR-0015 and was renamed to the next free slot (ADR-0028)
  before anything referenced it.

## Phase 10 increment (2026-10-29, second): the GUID drift gets standalone detectors, the leaf revocation gets a REAL firmware-served boot leg, and the gates get a weekly CI drift run

- **The anti-self-consistency trap got its own differential control.**
  The GUID byte-order bug this morning killed was invisible to every
  repo-internal check because the stub's constants and the host blob
  generators were wrong TOGETHER (each validated the other against
  identical bytes). `uefi/tests/trust_selfcheck.rs` now builds two
  signature-list fixtures whose input bytes come from SPEC-CANONICAL
  ASCII GUID strings parsed by `spec_guid_bytes` in the harness —
  hand-rolled from the spec text, deliberately NOT a copy of any repo
  constant — and classifies them through the walk: canonical
  EFI_CERT_X509 bytes must classify as the enforced whole-certificate
  type (marked by a byte-match refusal naming the stub's own constant
  at the FAIL site), canonical EFI_CERT_X509_SHA256 bytes must classify
  as the hash-typed revocation walk (future-dated 2400, so walked and
  counted, never in force). On any recurrence the fixtures flip from
  "classified" to "unknown type" and the FAIL names exactly which spec
  wire bytes the stub's constants must serialize. Both controls PASS
  (harness run: `OK spec-guid-x509` / `OK spec-guid-hash`).
- **`make-dbx-auth-update` in `patch_carrier_negatives.py` builds the
  authenticated-update header virt-fw's `--set-dbx` parses** — with an
  honest scope note, because the format is not what it looks like:
  virt-fw's `varstore/authfiles.py` writes EFI_TIME + dwLength=24 +
  wRevision 0x0200 + wCertType 0x0EF1 + EfiCertPkcs7 GUID, then the RAW
  signature-list payload in CertData, and `parse_authdata` slices the
  payload at 16+dwLength and feeds it straight to its SigDB parser —
  a genuine edk2 CreateTimeBasedPayload file (a real PKCS7 body,
  dwLength 24+pkcs7) can NOT survive that parser, and the first draft
  here was exactly that wrong shape. The varstore composition IS the
  trust boundary of these gates (every enrollment in them — PK/KEK/db/
  MokList/dbx — is written by virt-fw-vars, not by a SetVariable
  exchange), so the header exists to make the payload readable by the
  store tooling and firmware, not to claim a signed variable-update
  protocol exchange. The writer self-checks by parsing its own output
  with virt-fw's OWN EfiVar and asserting the recovered payload
  byte-equals the input (proven at runtime, not asserted in a comment).
- **The `.ms` secboot gate has a FOURTH boot leg: the enrolled leaf
  revoked BY HASH.** A second enforcing store, identical enrollment
  plus the LEAF's To-Be-Signed SHA-256 as an EFI_CERT_X509_SHA256 dbx
  entry (revoked 2020 — real-world Microsoft dbx update form, wrapped
  through `make-dbx-auth-update`), and the leaf-signed stub must be
  refused. The gate accepts BOTH enforcement paths, named: firmware
  refusal (Access Denied before the stub runs) or the stub's own
  revocation walk over the REAL firmware-served dbx variable (this
  OVMF build served the bytes; the walk refused with
  "a hash-typed entry covers the signer certificate's To-Be-Signed").
  Convenience: the live walk now ALSO prints the served shape —
  `dbx: firmware served 92 signature-list bytes; type GUID (0x92
  0xa4 0xd2 0x3b ...)` — so served-vs-composed mismatches are on the
  screen instead of inferred from the walk counters.
- **The leg's first run FAILED — recorded as run, root-caused, and
  the fix is real, not a re-run.** The revocation boot used the WRONG
  store: the new leg's `boot_qemu` call passed no store argument, so
  it booted the FIRST store, whose `--sb`-implied dbx is the virt-fw
  DUMMY (a hash-of-empty EFI_CERT_SHA256 entry — an "unknown type" to
  the walk — exactly matching the observed `walked 0 X.509 ...
  skipped 1`), and the stub booted Ready. Root cause found by com-
  posing and booting the leg manually, byte-comparing the store
  content (`payload identical: True` at the varstore's lone dbx
  record), and serving-side hex in the new transcript line; the fix
  is the missing `$REV_STORE` argument, and the standalone boot
  refused BEFORE the gate was re-run, not because of it.
- **The gates are now on a weekly CI schedule** (`.github/workflows/
  ci.yml` `schedule: cron 17 6 * * 1`): the boot-emulation gates live
  on EXTERNAL inputs that drift without any push to this repo — OVMF
  firmware updates, virt-fw-vars and sbsign releases, the rustc
  interaction the toolchain pin documents — and the push-triggered
  runs never see that drift. Monday 06:17 UTC, all gates unchanged.
- **Verified:** full battery on the changed sources. Host
  `verify_carrier.sh` with the two new spec-GUID controls green;
  plain UEFI gate PASS; `.ms` gate PASS on ALL FOUR legs (positive
  Ready with both MokList markers; unsigned and tampered refused by
  the firmware; the leaf-TBS-revoked store refused by the stub's walk
  with the served-bytes transcript printed).

## Phase 10 increment (2026-10-30): the rollout's tooling half -- a rotation ceremony that issues the replacement leaf and the dead one's dbx entry, a revocation leg that ASSERTS who refused and which bytes it saw, and the payload-layout defect that probe exposed (both dbx hash forms are firmware-enforced once the bytes are the spec's)

Three gaps this phase's own tracker named are closed here, and one
firmware behaviour is recorded honestly rather than glossed.

- **`tools/rotate_leaf_ceremony.sh` -- ADR-0028's rotation and
  ADR-0029's compromise procedure as one executable step.** It issues a
  replacement leaf under the root (fresh RSA-2048 key, 0600, the same
  extension shape the stub's leaf walk vets), signs the kernel image
  with it, and composes the DEAD leaf's `EFI_CERT_X509_SHA256` entry
  (EFI_TIME 2020 + SHA-256 of the To-Be-Signed TLV) plus its
  authenticated-update wrapping -- the exact shapes the `.ms` gate's
  fourth leg proves enforcement for. Ceremony posture is structural,
  not advisory: it refuses to run without an explicit root pair (it
  will not generate one), refuses a root key whose mode carries
  group/world bits, and writes nothing into the enrolled chain (all
  artifacts land in `uefi/dist/leaf-rotation/`).
- **Its checks encode ADR-0029's scope decision, each a named FAIL.**
  The replacement's To-Be-Signed hash must DIFFER from the dead leaf's
  -- otherwise the hash-typed entry would revoke the replacement, which
  is the failure the hash form exists to avoid -- AND its serialNumber
  must differ, or ADR-0014's issuer+serial form would revoke it too.
  The replacement must verify under the root (`openssl verify`) and its
  carrier must verify over the kernel bytes (`openssl cms -verify`), and
  the composed blob must be accepted by virt-fw's own parser and by
  virt-fw-vars on a store COPY, with the read-back variable's size
  matched against the composed SigDB.
- **Verified (real run):** `PASS: rotation ceremony complete` -- dead
  leaf serial `13452F6F…460` / TBS `c623c16e…cc49`; replacement
  `13452F6F…462` / TBS `d996e689…be07`; chain OK; carrier 1357 bytes
  verifies; `make-dbx-hashlist` list 92 bytes; `make-dbx-auth-update`
  payload recovered byte-exact by virt-fw's EfiVar; store probe
  `dbx: blob: 92 bytes`. Run twice (the cosmetic hash-print fix between
  runs), both green; every run generates a NEW key, so a stale artifact
  cannot pass by reuse.
- **The `.ms` gate's revocation leg now ASSERTS what it used to
  print.** It requires ADR-0029's unconditional transcript line, checks
  the served byte count equals the SigDB it composed (auth header
  excluded), and normalizes the printed type GUID to hex to require
  EFI_CERT_X509_SHA256's canonical wire bytes
  (`92a4d23bc0967940b420fcf98ef103ed`) -- written in the gate as spec
  bytes, not read from a repo constant, per the ADR-0028 drift lesson.
  Which party refused is now a verdict field, not a note; the stub
  branch additionally requires the stub's own alive line, so a boot
  that never named the hash-typed refusal cannot be counted as either
  path. Re-run verdict: `revocation leg: refused by stub; the firmware
  served the composed 92-byte EFI_CERT_X509_SHA256 list (GUID
  92a4d23bc0967940b420fcf98ef103ed) byte-for-byte`.
- **The assertion was proven discriminating, not self-satisfying:** the
  same normalization applied to a one-byte-mutated GUID
  (`…f1 0x4 0xed`) yields `…04ed` and compares NO.
- **Every gate re-run green on the changed sources, including the ones
  this increment did not touch** (the edits sit in the stub's boot
  path's inputs, so the whole battery was owed): `.ms` gate PASS on all
  four legs; plain OVMF gate PASS; BIOS gate PASS; and the transcript
  parity gate PASS with `BIOS 353 chain lines == UEFI 353 chain lines`
  and a **0-byte** residual diff. Host `verify_carrier.sh` PASS in all
  three runs (`positive + 6 controls + 7 negative-carrier refusals`,
  dbx 5 refusals + unknown-type skip + future-dated-hash skip, MOK
  enrollment/stranger/conflict).
- **The ceremony is now a CI step** (`.github/workflows/ci.yml`,
  `kernel-boot`, after the boot gates): it reads the key material and
  kernel image the gates above generate and fails the build on any
  named check -- so a broken issuance or revocation path breaks a
  build, not a ceremony.
- **The ceremony's refusal PATHS are tested**
  (`tools/ceremony_refusals_test.sh`, its own CI step right after). A
  positive run PASSES first, so the negatives are specific rather than
  "the script always fails"; then a `--root-key` that does not exist, a
  root key with group bits (640, on a TEMP COPY -- the original is
  never touched), and a replacement leaf forced onto the DEAD leaf's
  serialNumber must each fail with its own named line. Every case
  asserts BOTH the exit class and the named text, so an unrelated
  crash cannot satisfy a case. Verified: 4/4 cases OK, `PASS: rotation
  ceremony refusal paths verified`.
- **Recorded, not glossed: a FIFTH boot gives the stub's OTHER dbx
  parser branch a firmware-served leg, and on that one the FIRMWARE is
  the refusing party.** A third enforcing store carries the leaf
  certificate as `EFI_CERT_X509` (the whole-certificate form,
  ADR-0014's byte-match rule, `uefi/src/dbx-neg-signer-byte.der`
  wrapped by `make-dbx-auth-update`), with the SAME enrollment (signer
  in db AND MokList), and the leaf-signed stub must be refused. Both
  revocation legs share one checker, `dbx_leg_check`, so the served
  shape, the refusing party and the booted-anyway failure are asserted
  identically for both branches.
- **WITHDRAWN, kept as run: the FIRMWARE-refusal path did not fire on
  the HASH-TYPED leg, in either hash form -- and the probe that chased
  it found OUR bug, not a firmware gap (see the layout record below).
  The reading that follows was superseded by the corrected re-run in
  this same section, where BOTH legs are refused by the firmware; it is
  kept because it is how the defect was found.** The `.ms` leg's revocation
  store (leaf TBS hash, 2020-dated, in force) produced `stub alive
  under UEFI; SecureBoot=1` -- the stub's walk refused, the firmware
  did not. A probe store was then composed with a different entry
  shape: `EFI_CERT_X509_SHA256` carrying the SHA-256 of the leaf
  certificate's WHOLE DER (`30b373e6…`) instead of its TBS, 2020-dated,
  signer still enrolled in db AND MokList, and the same leaf-signed
  stub again reached the handoff (`stub alive under UEFI;
  SecureBoot=1`, 150 s cap). So on this distro `OVMF_CODE_4M.ms.fd`
  the SecureBoot verification's decision is consistent with its
  enrolled-certificate path and does not consult cert-hash dbx entries
  -- stated as observed behaviour, not root-caused inside the firmware,
  which this repo does not build or instrument. Consequence for the
  gate: it keeps BOTH paths in its contract and names the one that
  fired (now asserted), so this fact is on the transcript instead of
  being inferred from a refusal that did not happen. The stub's walk
  remains proven on the same real served bytes, which is path 2's
  claim and unaffected. **This paragraph's conclusion ("does not
  consult cert-hash dbx entries") is wrong and is withdrawn: the
  firmware does consult them, and the earlier silence was the
  field-order defect below.**
- **A bug in THIS increment's own first run, recorded as run.** The
  served-shape assertion was ordered before the path determination and
  required the stub's `<dbx: firmware served ...>` line
  unconditionally -- so the whole-cert leg failed with `served shape
  MISMATCH` on the FIRST run even though the firmware refusing is
  exactly that leg's claim: the stub never ran, so the line it prints
  could not exist. That is the assertion being wrong, not the gate's
  subject. Fixed by determining the refusing party FIRST and making
  the shape evidence path-specific: the stub path must show the served
  bytes matching the composed SigDB, while the firmware path must show
  NO stub output at all (already required) and takes its shape evidence
  from the composition-time virt-fw read-back; a firmware refusal next
  to a printed served line is now itself a FAIL as a contradiction.
  Worth recording because it is the same class as the earlier
  "wrong-store" root cause: a gate that asserts a fact out of its
  reach fails loudly for the wrong reason, and only the honest reading
  of the transcript says which.
- **First five-leg run, on the PRE-correction bytes: exit 0 -- and the
  split it measured was the defect's fingerprint, not a firmware fact.**
  `positive status 33` (Ready, SecureBoot=1, PK present, MokList facts);
  unsigned and one-byte-tampered both refused with `Access Denied` and
  no stub output; `[hash-typed] leg: refused by stub; served shape
  verified` (firmware served 92 bytes, GUID
  `92a4d23bc0967940b420fcf98ef103ed`, matching the composed SigDB
  byte-for-byte); `[whole-cert] leg: refused by firmware; served shape
  firmware-refused`; and the final PASS line naming both revocation
  forms. This is the FIRST time the FIRMWARE-refusal path has fired in
  any gate in this repo -- the either-path contract now has BOTH paths
  exercised against real firmware, which is what ADR-0029's "Who
  refuses, and what counts" section asked for and what the previous
  increment could only name. Every line of this run was measured on the
  swapped bytes described below: the hash-typed leg's `refused by stub`
  was THIS repo's parser accepting THIS repo's layout, and the re-run
  supersedes it.
- **THE PROBE'S REAL FINDING: a payload-layout bug in THIS repo, not
  a firmware gap.** Chasing why the firmware ignored the hash-typed
  entry, `edk2-stable202402`'s `SecurityPkg/Library/
  DxeImageVerificationLib/DxeImageVerificationLib.c` was read directly:
  `IsCertHashFoundInDbx` DOES enforce hash-typed dbx entries, and hashes
  the certificate's To-Be-Signed content -- exactly ADR-0029's
  semantics, so the "OVMF does not consult cert-hash dbx" reading was
  wrong. What was wrong was OURS: `EFI_CERT_X509_SHA256` is
  `EFI_SHA256_HASH ToBeSignedHash; EFI_TIME TimeOfRevocation;` -- the
  32-byte digest FIRST (UEFI 2.10 32.4.1 and edk2's MdePkg
  `Guid/ImageAuthentication.h`, SignatureSize 16 + 48; edk2 compares
  the digest at `SignatureData + 0` and reads the date at
  `SignatureData + DigestLength`), while `make-dbx-hashlist` wrote
  EFI_TIME first and `dbx.rs`'s `read_revocation` read it first. The
  two agreed with each other and with nothing else, so every host
  control, every stub-side boot control and the whole served-shape
  assertion passed over bytes no real firmware can match -- the
  ADR-0028 "self-consistent-but-wrong" class, in the one place two of
  our own implementations could agree with nobody outside.
- **Corrected the same day, generator and parser together:**
  `tools/patch_carrier_negatives.py`'s `make-dbx-hashlist` now writes
  hash-then-EFI_TIME (with the field order called out in its docstring,
  because a silent swap is what hid it), and `uefi/src/dbx.rs`'s
  `read_revocation` now parses hash-then-EFI_TIME (module doc, branch
  comment and the two structural refusal messages corrected with it).
  All dbx blobs were regenerated by `make_carrier.sh`; the host harness
  is green on the corrected bytes (`dbx 3`/`dbx 4` refusals, unknown
  type skip, future-dated skip, MOK hash blob); the boot gates re-prove
  them on real firmware. This also removes the earlier conclusion that
  the hash-typed guarantee was stub-only on these images: it is the
  firmware's too, once the bytes are the spec's.
- **Re-run on the corrected bytes (the one that counts): the FIRMWARE
  now refuses BOTH legs.** `[hash-typed] leg: refused by firmware;
  served shape firmware-refused` and `[whole-cert] leg: refused by
  firmware; served shape firmware-refused`; `positive status 33`
  (Ready, SecureBoot=1) and the unsigned/tampered refusals unchanged;
  the probe leg still reaches Ready while reading the composed
  975-byte `EFI_CERT_X509` entry through the firmware
  (`a159c0a5e494a74a87b5ab155c2bf072`); gate exit 0. So on these OVMF
  images both dbx forms ADR-0029 names are enforced by the PLATFORM
  once the bytes are the spec's, and the earlier "the firmware only
  enforces the whole-certificate form" conclusion is withdrawn, not
  softened. The honest cost: path 2 (the stub's own walk) no longer
  fires on this firmware -- the boot manager refuses first -- so its
  real-firmware evidence is the probe leg (a non-matching entry read
  through the firmware) plus the host harness over the same
  regenerated bytes. The walk stays as defence in depth for firmware
  without hash-typed enforcement, which is a platform question these
  gates cannot assert.
- **The gate's own summary lines were path-specific after this run:**
  they printed "the firmware served the composed ... byte-for-byte"
  even on a firmware-refused leg, where the stub never ran and nothing
  was served to it. Each leg's clause is now chosen from its measured
  shape (`verified` -> "the stub read ... then refused it",
  `firmware-refused` -> "the stub never ran, so the evidence is the
  composed entry checked at composition time plus the firmware's own
  refusal"). The re-run that exercised the change printed, verbatim:

  ```
  revocation leg (hash-typed): refused by firmware; the stub never ran, so the evidence is the composed 92-byte EFI_CERT_X509_SHA256 list (GUID 92a4d23bc0967940b420fcf98ef103ed) checked at composition time plus the firmware's own refusal
  revocation leg (whole-cert): refused by firmware; the stub never ran, so the evidence is the composed 932-byte EFI_CERT_X509 list (GUID a159c0a5e494a74a87b5ab155c2bf072) checked at composition time plus the firmware's own refusal
  ```

  with the probe leg reading its composed 975-byte `EFI_CERT_X509` entry
  through the firmware and reaching Ready, and gate exit 0.
- **The hash types the walk SKIPS are now pinned, all seven of them.**
  `uefi/tests/trust_selfcheck.rs` synthesizes one-list blobs for
  `EFI_CERT_SHA1`/`224`/`256`/`384`/`512` and
  `EFI_CERT_X509_SHA384`/`512`, with GUIDs decoded from the SPEC's own
  ASCII strings (the `spec_guid_bytes` differential, not any repo
  constant) and payloads filled to each type's spec width from the
  signer's To-Be-Signed hash, and asserts the walk reports every one
  as a skipped fact with zero X.509 and zero hash-typed entries walked.
  Those payloads are deliberately MATCHES, so an implementation that
  later adopts one of these types starts from a fixture that will
  refuse rather than a paper claim. Runs in `verify_carrier.sh`, so it
  gates the build and both boot gates: `-- OK dbx-skip-types: all 7
  hash-type classes the walk does not enforce ... are REPORTED as
  skipped, none enforced and none refused`.
- **The UPDATE side now has a gate of its own:
  `tools/dbx_update_gate.sh`** (plus a CI step). It builds a real
  KEK-signed `EFI_VARIABLE_AUTHENTICATION_2` package over the
  X.509-SHA256 revocation list -- 16-byte EFI_TIME, a
  `WIN_CERTIFICATE_UEFI_GUID` of dwLength 8+16+PKCS7, revision 0x0200,
  type 0x0EF1, `EFI_CERT_TYPE_PKCS7_GUID`, CertData = a genuine DER
  PKCS7 over (list || EFI_TIME) -- and proves the binding with
  openssl. Measured: `positive` verifies; a one-byte-altered list
  fails; a BACKDATED header timestamp fails (the stamp is signed, so
  rollback attempts cannot replay an update); a non-KEK signer fails by
  certificate identity. Two of my own first-cut mistakes are recorded
  because both are the same trap this session keeps meeting: the check
  compared `SignatureSize` to edk2's SigDataSize (48) instead of
  `SignatureSize - 16` with the owner GUID (64), and the stranger
  negative initially passed because `openssl cms -verify` accepts any
  signer certificate the message carries -- `-certfile` does not
  constrain identity. The gate now compares the package's signer DER
  to the KEK's, so "a signature verified" can never stand in for "the
  KEK signed it".
- **Blocked, with measurements rather than an assumption: the
  cross-build confirmation.** The tree carries exactly two OVMF CODE
  images -- `uefi/tools/OVMF_CODE_4M.fd` and `OVMF_CODE_4M.ms.fd`,
  3,653,632 bytes each, sha256 `ad23261d…` and `1286f2a3…`, both from
  the same 2026-10-22 package drop -- and those are the pair every
  gate already uses. Repeating the hash-typed enforcement measurement
  on a DIFFERENT firmware needs a third build (another edk2 revision
  or a vendor image) provisioned with PK/KEK/db, which this repository
  does not have. So the finding above stands on ONE firmware build and
  says so; no second build was invented to make the claim look
  broader.
- **Scope decision recorded: ADR-0030 (Rejected) keeps the enforced
  surface at ADR-0029's two types.** The seven other UEFI 2.10 32.4.1
  entry classes stay reported facts. Reasons stated in the ADR: the
  boot path has no SHA-384/512 implementation and adding two digest
  implementations to the trust walk buys nothing real distributions
  ship; SHA-1 would enforce an identity weaker than the chain just
  verified; the raw-digest classes name an IMAGE, not a certificate
  identity, so enforcing them here would silently redefine the walk's
  subject; RSA2048 blobs carry no certificate identity. The new
  fixtures are the decision's guard -- implementing any of the seven
  fails `dbx-skip-types`, so widening has to come with a superseding
  ADR instead of arriving quietly.
- **Firmware acceptance of the update package remains uncovered**, and
  the gate says so in its own PASS line: driving a real
  time-based-authenticated SetVariable needs a store whose PK/KEK the
  signer is enrolled in, and the enforcing gates' stores are composed
  by virt-fw-vars (ADR-0029's composition boundary), while the stub's
  own db write is the SetupMode arm where edk2 verifies no signature.

## Phase 10 increment (2026-10-31): firmware ACCEPTANCE of the revocation update -- the gap the update gate named -- and the two defects in our own builder that closing it exposed

`tools/dbx_update_gate.sh` proved a KeK-signed
`EFI_VARIABLE_AUTHENTICATION_2` package's binding with `openssl` and
said, in its own PASS line, that firmware ACCEPTANCE at SetVariable time
was NOT covered. That gap is closed here, and closing it found two
real defects in this repository's UPDATE-side bytes -- each one fully
self-consistent between our builder and our gate, and each one refused
by the first real firmware that saw it (the ADR-0028
"self-consistent-but-wrong" class, third occurrence).

- **One builder for the whole repo.** `tools/make_dbx_update_package.sh`
  composes and signs the package; `dbx_update_gate.sh`
  and `build.sh` both call it, so the host gate and the boot path
  cannot drift into two self-consistent package shapes.
- **The boot path exercises it against real firmware.** `build.sh`
  embeds two packages over the same list -- one signed by the carrier
  root (the enrolled KeK in the enforcing stores' analogy), one by the
  negative-only stray key -- revoking the stray certificate's
  To-Be-Signed hash, which covers nothing this chain trusts. `boot.rs`'s
  `dbx_update_via_kek` writes them to `dbx` with the
  time-based-authenticated attribute, so the FIRMWARE verifies the
  PKCS7 and decides; the write is opt-in per store through a one-byte
  trigger variable (`NyrqisDbxUpdate`, in the stub's own
  `VARCHK_VENDOR_GUID` namespace) that the `.ms` gate's POSITIVE store
  composes with `virt-fw-vars --set-json`. No other store carries the
  trigger, so the revocation and probe stores' own dbx entries are
  never replaced; the trigger is deleted after a verified write so a
  re-boot cannot replay a now-stale stamp.
- **Defect 1 -- the signed content was wrong.** UEFI 2.10 8.2.2 and
  edk2's `VerifyTimeBasedPayload` sign
  `(VariableName, VendorGuid, Attributes, EFI_TIME, Data)`: the UTF-16LE
  name WITHOUT its terminator, the vendor GUID in wire order, the u32
  attribute word, the descriptor's stamp, then the payload. Every
  earlier version of this repo's builder -- and `dbx_update_gate.sh`'s
  own verification -- signed `payload || EFI_TIME`. `openssl cms
  -verify` passed because it verified the same wrong content; edk2
  answered `EFI_SECURITY_VIOLATION` (`0x800000000000001a`) for every
  package, including the correctly-KeK-signed one. Fixed in
  `make_dbx_update_package.sh` (which now documents the serialization
  and the failure), with the attribute word 0x27
  (NV|BS|RT|TIME_BASED_AUTHENTICATED) pinned to what the stub passes.
- **Defect 2 -- the descriptor carried the wrong PKCS#7 shape.** edk2
  reads `WIN_CERTIFICATE_UEFI_GUID.CertData` as a PKCS#7 **SignedData**
  SEQUENCE and checks the SHA-256 OID at a fixed `+13`. `openssl cms
  -sign` emits a `ContentInfo` wrapper, shifting every offset by eight
  bytes. The builder now strips the `[0]` element and embeds the bare
  `SignedData`, keeping the wrapper only for its own `openssl`
  self-verification. Fixing only defect 1 would still have been
  refused; the two were found by iterating against the firmware, each
  refusal naming its status.
- **An independent oracle replaced self-agreement.** The gate now
  asserts the signed content's layout byte-for-byte from the SPEC's
  strings (`dbx` UTF-16LE, the ISD GUID's wire bytes, attrs 0x27, the
  header stamp, the payload) and cross-checks against `sbsigntools`'
  `sbvarsign`: that tool's signature must verify over content rebuilt
  from the pinned layout using ITS OWN stamp. A serialization invented
  here would fail the oracle even with builder and gate agreeing --
  measured: `-- OK oracle: sbvarsign's own signature verifies over the
  content rebuilt from the pinned layout`.
- **Measured on real firmware (`.ms` positive boot, OVMF_CODE_4M.ms):**

  ```
  dbx-update: trigger present -- this store asks for the authenticated write
  dbx-update: PK present (user mode) -- the firmware verifies the AUTH package against the enrolled KeK
  dbx-update: non-KeK-signed package REFUSED by the firmware = 0x800000000000001a (expected: its signer is not the enrolled KeK)
  dbx-update: SetVariable(dbx) ACCEPTED 0x5c6 bytes (KeK-signed auth2) -- EFI_SUCCESS
  dbx-update: read-back VERIFIED -- the firmware's dbx now carries the 0x5c-byte entry the package signed (variable 0x5c bytes)
  dbx-update: trigger consumed (deleted) -- a second boot of this store will skip the write
  ```

  so the acceptance half of ADR-0029's surface is measured, not
  asserted: a real firmware verified the PKCS7 over the signed
  serialization, compared the stamp against the store's, stored the
  list, and served it back; the same list signed by a non-KeK key was
  refused with `EFI_SECURITY_VIOLATION` in the same boot, before the
  accepted write, so the refusal cannot be a timestamp artefact.
- **What remains uncovered, stated rather than implied:** the
  distribution/signing ceremony (who signs, how packages reach an
  operator, cadence) and real firmware beyond the two builds in this
  tree; the package is stamped deterministically (2027-01-01) so it is
  newer than any dev store's dummy dbx entry and independent of the
  build clock. The host gate's PASS line now points at this boot leg
  for acceptance instead of claiming coverage itself.

## Phase 10 increment (2026-10-31, second): the signing ceremony, a cross-build control for the acceptance claim, and ADR-0030's premise checked instead of asserted

Three follow-ups from the increment above, each landed with its own
measurement.

- **Signing/distribution ceremony.** `tools/sign_dbx_update_ceremony.sh`
  composes a revocation for a NAMED certificate and refuses to hand over
  a package unless six properties hold: the KEK key's custody (no
  group/world bits), the target is not the trust anchor, the signing
  certificate IS the explicitly-named enrolled KEK (byte-for-byte DER
  sha256 -- naming the signer as its own reference would be a
  tautology), the stamp strictly advances over `--previous-stamp`, the
  composed list carries exactly the target's To-Be-Signed hash once, and
  the PKCS#7 binds over the signed serialization with the KEK's own
  certificate. Every refusal is a named FAIL.
  `tools/update_ceremony_refusals_test.sh` proves the checks bite: a
  positive control plus five refusals (missing input, loose custody,
  anchor target, non-enrolled signer, stale stamp), each asserting the
  exit class AND the specific message. The ceremony writes an
  `update-manifest.txt` (package sha256, revoked subject/serial/TBS hash,
  stamp, signer, enrolled-KEK provenance, delivery note) and states what
  it does NOT do: deliver or schedule.
  ```
  PASS: signing/distribution ceremony complete
    package        : uefi/dist/dbx-update-ceremony/dbx-update.auth2 (1478 bytes)
    revoking       : CN = Nyrqis dev carrier leaf, OU = Nyrqis bringup (serial 13452F6F..)
    signer         : CN = Nyrqis dev trust anchor (enrolled KEK match verified byte-for-byte)
    stamp          : 2027-01-01
    custody        : key mode 600 (no group/world bits)
  PASS: revocation update ceremony refusal paths verified -- the positive run passes, and a
        missing input, loose custody, an anchor target, a non-enrolled signer and a stale stamp
        each fail with their own named message
  ```
- **A cross-build control, so "acceptance" is firmware support rather
  than a gate assuming success.** Running the SAME enrolled store and
  the SAME package against the other firmware build in this tree
  (`OVMF_CODE_4M.fd`, sha256 `ad23261d..`) measures:
  ```
  nyrqis ubld: stub alive under UEFI; SecureBoot=absent
  dbx-update: trigger present -- this store asks for the authenticated write
  dbx-update: non-KeK-signed package REFUSED by the firmware = 0x8000000000000002
  dbx-update: SetVariable(dbx, NV|BS|RT|TBA, 0x5c6 bytes) = 0x8000000000000002 -- REFUSED by the firmware (named fact)
  ```
  i.e. that build has no time-based-auth variable surface and refuses
  BOTH packages, the correctly-signatured one included, with
  `EFI_INVALID_PARAMETER`. A full gate run against it therefore fails
  its positive leg -- the correct verdict, since the build cannot
  satisfy an enforcement claim -- so it is wired as an opt-in control
  instead: `SECBOOT_CROSS_BUILD_CODE=<code image>` asserts the stub ran,
  the store asked for the write, the KeK package was NOT accepted, and
  the status is the measured `EFI_INVALID_PARAMETER`. It refuses to run
  against the same image as the main leg, and it FAILS with a message
  naming the resolution if a future build starts accepting. CI runs it
  on the weekly drift schedule only (the distro's plain build is an
  external input).
- **The gate's dbx shape probe was computed and then ignored.** Writing
  the cross-build control surfaced that the PASS condition tested
  `POS_OK NEG_OK TAMPER_OK REV_OK REVC_OK` but not `PROBE_OK`: the probe
  leg could print `FAIL: [probe] ...` and the gate still exited 0 -- the
  silent-ignore class this repository keeps meeting. `PROBE_OK` and
  `CROSS_OK` are now part of the condition and named in both the PASS
  and FAIL summaries.
- **ADR-0030's premise, measured.** `tools/dbx_inventory.py` parses a
  real dbx artifact (auth2 package or bare signature lists) and reports
  entry classes, sizes and counts. Against Microsoft's published
  artifacts (2026-10-31): the current amd64 `DBXUpdate.bin` is **443
  `EFI_CERT_SHA256` entries and nothing else**; `DBXUpdate2024.bin`
  carries the CA revocation as ONE whole-certificate `EFI_CERT_X509`
  entry plus 3 SHAs; `DBXUpdateSVN.bin` is 3 SHAs; and the inventory JSON
  the artifacts are built from holds 677 image hashes across four
  architectures, every one `SHA256`, one certificate, and the SVN
  counters. No `EFI_CERT_X509_SHA384`/`512` and no TBS-hash form ships at
  all. ADR-0030's Rejected decision therefore stands on evidence, and its
  own claim that Microsoft CA revocations use `EFI_CERT_X509_SHA256` is
  corrected: the shipped CA revocation is the whole-certificate form,
  enforced by the walk's other branch, while `EFI_CERT_X509_SHA256` is
  enforced because ADR-0028/0029's rotation issues it.

## Phase 10 increment (2026-11-01): the FLEET end of revocation, and the same
## acceptance assertions on a third firmware build

Three follow-ups from the increment above, each landed with a measurement.

- **The fleet end: a verifier for a store, not for a package.**
  `tools/verify_dbx_store.py` answers the operator's real question --
  does THIS store refuse THAT certificate -- for an edk2 varstore (read
  through `virt-fw-vars --output-json`, the same tooling the gates
  compose with) or for a bare list / auth2 package. Its verdicts are
  kept distinct because they mean different things:
  **revoked, effective**; **revoked, not yet** (the entry's `EFI_TIME`
  is later than `--at`, so firmware does not refuse it yet -- REFUSED by
  default, since "we revoked it" would be false); **present, not
  enforced** (the identity sits in a class the kernel's walk does not
  read, i.e. ADR-0030's report-only set -- REFUSED, because a store can
  look revoked while no machine refuses anything); **absent**, including
  a store with no dbx variable at all. `--absent CERT` asserts the
  rotation's replacement is NOT revoked, which is how an over-broad
  revocation is caught before it becomes a fleet outage.
  The To-Be-Signed hash comes from the verifier's OWN DER walk rather
  than from the helper the ceremony signs with: a checker that shares
  the signer's implementation can only restate it, and this repo has
  already shipped one defect of exactly that shape (`EFI_CERT_X509_SHA256`'s
  field order, 2026-10-30). `tools/verify_dbx_store_test.sh` proves every
  verdict bites -- both enforced forms pass; absent, scheduled,
  ignored-class, unreadable and over-broad each fail with their own named
  message -- and cross-checks the derived hash against
  `patch_carrier_negatives.py`'s independent implementation. CI runs it
  on every push.
  Real-data check (the one that matters, because the data comes from
  firmware rather than from this tool):
  ```
  $ ./tools/verify_dbx_store.py --store uefi/dist/VARS.ms-secboot-dbxrev.fd \
        --cert uefi/tools/trust/carrier-dev-leaf-cert.pem
    match: EFI_CERT_X509_SHA256 -- To-Be-Signed sha256, revoked 2020-01-01
    effective: 1 enforced entry(ies) revoke ... at 2026-11-01
  PASS
  $ ./tools/verify_dbx_store.py --store uefi/dist/VARS.ms-secboot-dbxcert.fd \
        --cert uefi/tools/trust/carrier-dev-leaf-cert.pem
    match: EFI_CERT_X509 -- whole-certificate byte match
  PASS
  ```
  and the ceremony's package (stamped 2027-01-01 by design) is refused as
  `revoked only FROM 2027-01-01 ... pass --allow-pending`, which is the
  scheduled-not-effective verdict doing its job.
- **The operator sequence is written down.**
  `docs/how-to/revoke-a-secure-boot-carrier.md` runs from custody through
  ceremony, artifact check, delivery and fleet check, with a table that
  maps each refusal to its remedy. Two limits are stated there rather
  than implied: delivery and scheduling are platform process, and a
  firmware without the time-based-auth surface answers
  `EFI_INVALID_PARAMETER` to a CORRECT package -- which looks like a
  verification failure and is really unsupported firmware.
- **A third firmware build carries the same assertions.** The two
  images in this tree are both Ubuntu `ovmf 2024.02`, byte for byte
  (`OVMF_CODE_4M.ms.fd` `1286f2a3..`), so the cross-build control's
  earlier measurement was "a build WITHOUT the surface". Measured
  2026-11-01 against Debian trixie's edk2 **2025.02**
  (`OVMF_CODE_4M.ms.fd` sha256 `1a462955..`, with that package's own
  vars template), full gate:
  ```
  OVMF_CODE_OVERRIDE=.../OVMF_CODE_4M.ms.fd \
  OVMF_VARS_OVERRIDE=.../OVMF_VARS_4M.fd \
      ./tools/qemu_uefi_ms_secboot_test.sh   ->  EXIT=0
  ```
  with the acceptance sequence reproduced on that firmware:
  ```
  dbx-update: non-KeK-signed package REFUSED by the firmware = 0x800000000000001a
  dbx-update: SetVariable(dbx) ACCEPTED 0x5c6 bytes (KeK-signed auth2) -- EFI_SUCCESS
  dbx-update: read-back VERIFIED
  dbx-update: trigger consumed (deleted)
  ```
  No gate change was needed -- `OVMF_CODE_OVERRIDE`/`OVMF_VARS_OVERRIDE`
  already existed -- and every verdict in the transcript names the
  firmware's sha256. Not claimed: one build per measurement, not a
  matrix; firmware older than 2024.02 or otherwise patched is unmeasured.

## Bugs found and fixed during Phase 1 bring-up

Documented because each one is a real, non-obvious pitfall, and this is
exactly the kind of thing that should be recorded rather than silently
fixed and forgotten (matching this repo's existing convention of
recording real bugs in `REPOSITORY_STATE.md`/`SESSION_HISTORY_ARCHIVE.md`
for the Linux backend):

1. **`x86_64` crate version vs. rustc version mismatch (E0053).**
   `x86_64 = "0.14"` resolved to 0.14.13, whose `Step` trait impl uses
   a newer nightly signature (`(usize, Option<usize>)`) than rustc
   1.75.0's internal `Step` trait expects (`Option<usize>`). Fixed by
   pinning `=0.14.10`. See the comment in `Cargo.toml`.

2. **Invalid `\x` escape for a non-ASCII character.** An early draft of
   `boot_phase.rs`'s panic message tried to hand-encode "§" as
   `\xc2\xa7` inside a `&str` literal; Rust only permits `\xHH` escapes
   in the 0x00-0x7F range for `str` (arbitrary bytes aren't valid
   UTF-8 scalar values). Fixed by just writing the word "section"
   instead of the symbol.

3. **`-kernel` direct boot doesn't work for a Multiboot2 ELF64 kernel
   on QEMU 8.2.** `qemu-system-x86_64 -kernel target/kernel.elf` failed
   with "Error loading uncompressed kernel without PVH ELF Note" --
   QEMU's built-in `-kernel` loader path doesn't handle this case the
   way real GRUB2 does. Fixed by building an actual bootable ISO with
   `grub-mkrescue` and booting that via `-cdrom` instead. This is also
   just more representative of how the kernel would actually be
   booted on real hardware or in a VM, so it's the right fix, not only
   a workaround.

4. **GRUB reported "no multiboot header found" even though the header
   was byte-correct.** Root cause, found via `grub-file
   --is-x86-multiboot2` plus direct inspection with `readelf`/raw file
   search for the magic bytes: GNU `as` does not give a custom section
   name (`.section .multiboot_header`) the `SHF_ALLOC` flag by
   default. Without that flag the linker excluded the section from
   the ELF's `PT_LOAD` segment entirely and placed its bytes past the
   file's first 32KB (Multiboot2's required search window), even
   though the linker script listed it first. Fixed with
   `.section .multiboot_header, "a"`.

5. **`--gc-sections` then discarded the header anyway.** Once
   `SHF_ALLOC` was fixed, the linker's `--gc-sections` (dead-code
   elimination) removed the section entirely, since nothing in the
   program references a symbol from it. Fixed with `KEEP(*(.multiboot_header))`
   in `linker.ld` -- the standard idiom for exactly this situation
   (also needed for things like interrupt vector tables in other
   projects).

6. **Apparent infinite double-fault loop, actually two separate,
   unrelated things being misread as one.** Early debugging (before
   bugs 3/4/5 were found) showed QEMU's `-d int` trace repeating
   "Servicing hardware INT=0x08" forever. This was misdiagnosed at
   first as a genuine CPU double-fault loop (vector 8 = #DF in
   protected/long mode) — actually, since the kernel was never
   successfully loading at all yet (bugs 3-5), this was GRUB/BIOS's
   own real-mode PIT timer tick (also conventionally vector 8, but a
   completely different, totally normal thing in 16-bit real mode)
   being serviced while GRUB sat in its rescue shell waiting on
   keyboard input it was never going to get. Diagnosed correctly only
   after switching from `-serial stdio -display none` (which shows
   nothing from GRUB's VGA-text console) to `-nographic` (which
   surfaces the actual BIOS/GRUB text output) and reading GRUB's own
   error message directly instead of inferring from a QEMU trace.
   Lesson kept for next time: when a boot hangs with no diagnostic
   output, check what the *bootloader* is doing before assuming the
   *kernel* is at fault.

7. **A genuine interrupt-vector collision, fixed independently of bug
   6's misdiagnosis.** Separately, real hardware IRQ0 (the PIT timer)
   defaults to vector 0x08 on the legacy 8259 PIC before it's
   remapped -- the exact same vector number as the CPU's #DF (Double
   Fault) exception. Had the kernel ever run with interrupts enabled
   before remapping the PIC (or masking IRQs), a routine timer tick
   would be misdelivered as if it were a double fault. Never trust the
   bootloader's interrupt-flag state on handoff: `cli` is now the
   literal first instruction of `_start` in `boot.s`, and Phase 1 never
   issues `sti` anywhere. PIC/APIC remapping is Phase 2/3 work; until
   it exists, staying fully masked is the correct posture, not a
   shortcut.

8. **`FrameAllocator` trait not in scope (E0053-adjacent, Phase 2).**
   `paging.rs` called `frame_allocator.allocate_frame()` without
   importing `x86_64::structures::paging::FrameAllocator`, which is
   what the method actually lives on (implemented for
   `BumpFrameAllocator` in a separate file). Rust's trait-method
   resolution requires the trait itself in scope even when there's
   only one plausible implementor -- a one-line fix once the compiler
   pointed at it, but recorded because it's a common first-time
   `x86_64`-crate friction point worth knowing about in advance.

## Parity-gate catalogue update (2026-11-03): the host's OVMF/GOP grew p
ast the 2026-10-23 allowlist, so the two boot paths' ENVIRONMENT now tell
 the shell truer specifics than that recording

`tools/qemu_boot_parity_diff.sh` failed its residual-diff contract
(YIELDING the ring for exactly the case it exists for) with differences the
November-3 host's firmware stack causes -- none of them kernel divergenc
es, proven by BOTH sides' kernel self-tests being line-identical (the
`service-0x84` line, both schedulers, ipc and every non-hardware proof
line is byte-identical across the two paths):

1. **GOP resolution (1280x800 vs 1024x768).** OVMF now hands the UEFI
path its native 1280x800 where the BIOS/Multiboot2 path still resolves
1024x768. Resolution is loader-provided hardware shape, and every line
derived from it -- device-mode pane geometry, the shell's size-class/
hashes and checksums (content of a DIFFERENT pane), the display
resolution registry row, and the cursor's centered-then-moved positions
(center at RES/2 and every displacement off it) -- is a boot-environment
fact, not kernel behaviour.
2. **ATA IDENTIFY unanswered on the UEFI path's QEMU config.** The UEFI
gate's drive set is pflash pairs plus a raw `esp.img` (IDE default
interface) with no IDE-attached data drive on this host -- so the
primary-channel IDENTIFY gets no answer there, while the BIOS gate
default-QEMU config still does (its registry row reads `bound`).
The autoload summary and the ata-pio registry ROW therefore differ by
exactly the registries' HONEST report of a different hardware fact --
which is what `absent   no ATA drive answered IDENTIFY` means; the
kernel is reporting, not diverging.

Because these lines' SHAPE (line type and its internal format, argument
list) is still kernel-owned, the script does NOT drop them wholesale --
that would hide a future real change to any of them. It instead
NORMALIZES the environment-derived VALUES inside each line type:

- `device-mode` lines: resolution -> `RES`, pane geometry -> `PANERES`,
  `surface hash`/`content checksum` -> `HASH` (each side hashes its own
  pane and has its own read-back verification; the parity contract is
  the check that the SAME code produced both, not two equal panes).
- `devices: display --` line: resolution -> `RES`.
- `devices: autoload --` line: the bound/unbound/absent counts
  -> `BOUND-UNBOUND-ABSENT` (the trailing refused count stays, it is
  kernel-owned), so a driver register/unregister or a lie still
  changes BOTH paths identically-or-rings.
- `devices:   ata-pio` registry row: `ATA-ROW` (this row is allowed to
  read bound or per-hardware absent; the per-row LINE TYPE and columns
  must stay identical).
- `cursor:` lines: all `(x,y)` coordinates -> `(XY)`.

Every normalized line must still appear on BOTH paths in the same SHAPE
(same field order, same argument lists) -- a residual diff on any of
these line types' structure, or any NEW line at all, still FAILs the
gate, which is what keeps the "same chain on both boot paths" contract
honest while the machine hardware id differs. The gate re-passes with
these normalizations in place, and its PASS line quotes both sides'
chain-line counts.

## Capability rows on the 0x84 gate (2026-11-04): the release's endpoint-capability
## half, served through our OWN handle-table machine and proven at both rings

`src/experimental/caps.rs` ports `porting/release-1-71/src/experimental/capability.rs`'s
ENDPOINT half to this tree's identity model: holders are scheduler task ids,
handles are indexes into kernel-owned per-holder tables (never forgeable, never
sharedable — §5.4), payloads are kernel-copied and bounded (§3.1), queues are
bounded with a named refusal (backpressure, never a silent drop), transfers run
only in the dedicated field and only ATTENUATE (§5.2/§5.3), and §4.3 revocation
is a generation bump — O(1), outstanding caps dead with no holder cooperation.
NOT ported, named in the module for checkability: the device kinds
(BlockDevice/IoPort/FrameBuffer/Input/SharedMem/Reply), badges, containers,
notify, blocking receive.

The gate rows are `CAP_SEND` (0x47) and `CAP_RECV` (0x48); identity on these
rows is `scheduler::current_task()`, so un-sessioned ring-3 callers share task
0's table — documented in both modules, and the session machine the manifest
parks flips this to isolation WITHOUT another ABI change. Every check the
machine makes happens BEFORE any queue is touched (the release's oracle-proof
ordering): a refused send leaks no queue depth.

Three bugs were caught at first boot and fixed AT CAUSE, all in the proof and
self-test, none in the machine's semantics: (1) the widening check demanded
GRANT from a SEND-only cap and observed NoRight — the machine checks the
transfer right before the subrange check, so the instrument now uses both a
SEND-only holder (NoRight) and a GRANT-only holder (Widening), and the
non-owner `mint` refusal became an explicit check instead of an accidental
panic; (2) the payload read-back compared a BIG-endian packing of the drained
payload against a LITTLE-endian read of the program's buffer — same bytes,
wrong order, mismatch by construction; the record is now LE both sides;
(3) the freshly mapped results page is not backed by zeroed memory, so the
program zeroes the eight bytes the read-back covers before the receive —
garbage tails would make a CORRECT kernel's copy mismatch. All three are
commented at the emitting code.

Checks: `experimental caps: 21 clause groups proven positive+negative` on every
boot; `capability rows refused by name on an empty table OK` in the gate's boot
self-test (ESRCH/EBADF/EMSGSIZE/EBADF, the exact empty-table answers);
`tools/svc84_proof_test.sh` PASS — the verdict line now carries
`caps (ack 0x0 , recv 0x6 , forged 0xfffffffffffffff7 ,
oversized 0xffffffffffffffa6 , payload 0x73697172796e)` and the kernel compares
the program's MEMORY against its queue record, value-for-value; the ledger gate
now reads 9 served calls (0x40..0x48), all exercised by `prog_svc84.s`.

## Blocking receive (2026-11-04): the mailbox's NPS-003 §3 block-and-wake,
## proven by a real parked task on every boot

`src/experimental/ipc.rs`'s `receive` was upgraded from the bounded **poll
loop** (yield between polls — a waiting receiver still consumed one dispatch
per tick) to scheduler-level **park-and-wake**: the receiver registers itself
on the mailbox's waiter list under the same no-interrupts window that drains
the queue, then `block_current()`s (RUNNING → BLOCKED, dispatcher moves on
immediately — a parked receiver consumes ZERO dispatches); every `send` to
that mailbox drains the waiter list and `unblock_task`s each holder
(BLOCKED → READY), so a wake is always attached to a real arrival. The budget
parameter's honest shape changed with it: `max_wakes` now counts EMPTY wakes
(wake competition, or an explicit `wake_waiters` kick — the cancel/abort
form, what a real kernel's timeout tick would call per parked waiter); an
exhausted budget fails with the typed `ReceiveTimedOut`, never an indefinite
park, never a silent re-arm.

Proven every boot in `kernel_main` by the demo's new C/D pair — task 0
itself must never park (the boot IS task 0), so the legs are on spawned
tasks: task C parks on an empty endpoint with a one-wake budget; task D
consumes its parked registration with one empty `wake_waiters` kick (exactly
the budget unit leg 1 must spend → typed `ReceiveTimedOut`), then kicks C's
second park and SENDs the real delivery in the same window — the wake C
receives is attached to an arrival, the no-lost-wake half of the contract.
Task 0's own `self_test_arbiter_and_revocation` keeps only the
non-blocking legs (out-of-range endpoint fails immediately with
`NoSuchMailbox`; a message queued BEFORE the receive delivers at the first
probe), and says so in its comment — the parked legs are the C/D demo's.

Gate surface note: the `0x84` `IPC_RECV` row is UNCHANGED — it is and
stays the non-blocking drain with `ENODATA`/`ENOENT`; the gate's blocking
RECV refusal (`ENOSYS`, by name) waits for the trap-saved session machine
the manifest parks. The kernel-side primitive is now real, which was that
refusal's honest prerequisite.

Checks: BIOS boot gate PASS with `task C parked, got 1 empty wake ->
ReceiveTimedOut (typed, never an indefinite block), then got woken BY A SEND
and delivered -- OK` on the tape; UEFI boot gate PASS; parity diff
line-identical (416 chain lines); `svc84_proof_test.sh` PASS unchanged.
## The Input capability kind + the INPUT_POLL row (2026-11-05): the release's
## Phase-9/1-29 poll authority, minted kernel-only and served authority-first

`src/experimental/caps.rs` gained the release's `Kind::Input` — the poll-
authority TOKEN (the release's exact right set, applied at mint time): rights
bit READ ("may poll the raw event stream") joins SEND/GRANT, the kernel's
`bootstrap_input` (the release's `init` role) is the ONLY minter (rights
masked READ|GRANT, `endpoint` 0, generation 0 — the queue it guards is the
kernel's input hardware state, NOT a revocable mailbox, so it never stales,
and the transfer machine's liveness check is kind-aware), and `input_check`
is the authorization: WrongType when the handle names some other kind, NoRight
when it carries no READ. Each refusal is named, and the ordering is the
release's authority-first one: the check runs BEFORE any event is consumed,
so a refused poll can never reach the queue.
The first boot of this slice panicked `transfer receive: TargetDead`: the
Input-kind proof group had been placed AFTER the revocation/terminate groups
it rode (both kill exactly what the group needs — a live endpoint and a
current-generation client cap). The group now runs while those are live,
terminate stays terminal by construction, and the comment at the emitting
code names why the ordering matters. The kernel-side clause proof now reads
`27 clause groups proven positive+negative`.

`src/experimental/input.rs` ports the release's raw byte ring (1-50 shape):
SPSC FIFO, every byte tagged with device and IRQ-time tick, bounded 1024
entries with COUNTED overflow (dropped(), never a silent drop), seed_* for
boot tests, and the kernel-side 2-clause self-test (FIFO+tags+timestamps,
overflow accounting). PUSH points are the real IRQ lines: `keyboard`'s
IRQ used to keep only the LAST scancodes; now every byte goes into the ring
RAW (nothing decoded in the kernel — that lives in user space, both there
and in `ps2_aux`'s assembler).

`src/service84.rs` serves the release's INPUT_POLL shape as `CALL_INPUT_POLL`
(0x49): a0 = HANDLE, same identity as the other capability rows:
`current_task()` — so un-sessioned ring-3 callers still speak through task
0's shared table (the session machine the manifest parks flips this to
isolation WITHOUT another ABI change); a1 = pointer to the 16-byte record
{raw byte, device, IRQ-time ticks} written
under the user-range validator. Returns 1 byte popped, 0 when the caller is
AUTHORIZED but bare — a real answer, not a refusal. Unused widened args are
shape-refused (EINVAL). Boot self-test adds ESRCH (empty-table authority),
EBADF (truncating handle), EINVAL (shape claim) — "INPUT_POLL refused by name
on an empty table OK".

The ring-3 proof's new leg proves the whole shape at CPL 3: kernel equip
mints task 0 an INPUT cap (READ|GRANT) after `svc84_proof`'s endpoint equip
and seeds TWO known scancodes; the program polls through HANDLE 2 (1 with
byte 0x1e, 1 with 0x9e — device tag and ticks delivered WHOLE), a third
poll answers the authorized-and-bare 0 with an UNTOUCHED record buffer
(the empty path writes nothing — a poll that answered empty but scribbled
would be a lie), a forged handle is refused EBADF with no byte eaten.
Checks: `svc84_proof_test.sh` PASS with the verdict's `INPUT_POLL (1 0x1 ,
2 0x1 , 3 0x0 , forged 0xfffffffffffffff7 , bytes 0x1e/0x9e)` and
`INPUT matches`; the ledger gate reads 10 served calls (0x40..0x49), all
exercised by `prog_svc84.s`; `qemu_boot_test.sh` PASS with the input
ring's own clause line green.

## The gate's session machine, first slice + the real INPUT_POLL byte source (2026-11-05,
## same day, second increment): the ring-3 proof caller speaks through its OWN capability
## table, and the polled bytes arrive through the controller, not a seed

The PORT_MANIFEST parks the release's session machine ("what a process
is, where its capabilities live" — every ring-3 task a session, recorded at
the scheduler's ring-3 switches) as a last step. This increment takes its
FIRST SLICE, and it is exactly what the manifest said was missing: a
ring-3 caller that OWNS a session speaks on the capability rows with its
OWN capability table, not task 0's.

Mechanism: `service84::attach_gate_session(sid)` — the gate's SESSION is
KERNEL-OWNED and recorded AT THE RING TRANSITION (the proof's equip path
calls it once, immediately before entering ring 3; the proof never
returns, so no withdraw point exists yet, the same terminal-path honesty
the ipc/caps equips carry). The three capability rows (CAP_SEND 0x47,
CAP_RECV 0x48) and INPUT_POLL (0x49) now take their caller identity from
`gate_session_identity()`: the session's id when one is attached, the
scheduler's running task otherwise (normal boots record nothing, so every
ring-0 caller — the boot self-test, the kernel itself — keeps task 0's
identity, which IS the kernel's table — the release's shape, documented).

The proof's session holder is minted one past the scheduler's id
space (`svc84_proof`'s PROOF_SESSION_ID = 7, one past the boot's seven
task ids 0..6): the holder id space is the SCHEDULER's plus sessions',
so the parked full machine cannot collide with a session. The equip is
`caps::prepare_session_for_proof` (endpoint 0 owned by the session; its
SEND|GRANT cap at slot 1; the Input cap at slot 2 — retired
`prepare_task0_for_proof`, whose shared-table shape is exactly what the
session machine removes).

The DISTINCT TABLES property is ASSERTED by the kernel, not trusted from
the code's shape: immediately before ring 3, the kernel resolves a
capability AS TASK 0 and requires the check to answer BadProcess. That
is only answerable because task 0's holder was never created — under the
retired shared-table equip, task 0's holder would have existed and held
these very caps, so the same resolution answered BadHandle, not
BadProcess.

The sender claim moves WITH the identity, because the CAP_RECV record is
(sender << 32) | len: the message is now tagged with the SESSION's id
(7), so the proof's record reads 0x700000006 (length 6 unchanged). The
verifier expects THAT value by name — the sender is who the caller's
identity says it is, which is the machine working, not a regression.

The INPUT_POLL row's byte source is the REAL HARDWARE PATH, replacing
the seeds: `svc84_proof` injects the two scancodes (0x1E then 0x9E) via
the controller's output buffer (`keyboard::inject`, the 0xD2 command —
the same proven path the keyboard self-test exercises), each byte
raising IRQ1 exactly as a keystroke would; the handler queues them raw.
A bounded spin waits on the handler's own counter (`scancodes_received`
delta == 2) and fails BY NAME — a dead controller is a boot fact, not a
hang. The ring is drained first (`input::clear()`, boot-test-only, one
pop loop). The read-back's compare constant is `INJECT_BYTES` (renamed from
SEED_BYTES — the old name claimed a population source the code no longer
exercises), so the polled records are still judged against the KERNEL's
own bytes, one representation on both sides.

Checks (no boot-environment changes: normal boots never attach a
session): `svc84_proof_test.sh` PASS with the verdict's CAP record
(ack 0x0, recv 0x700000006, forged -7, oversized -10, payload the
"nyrqis" sentinel) and `INPUT_POLL (1 0x1 , 2 0x1 , 3 0x0 , forged
0xfffffffffffffff7 , bytes 0x1e/0x9e)`; `qemu_boot_test.sh` PASS
(BootPhase::Ready, the C/D/E blocking-receive tape pinned);
`qemu_uefi_boot_test.sh` PASS (GOP frame handed over, bound, shell
rendered); `qemu_uefi_ms_secboot_test.sh` PASS (signed carrier accepted,
unsigned/tampered/hash-revoked/whole-cert-revoked all refused by the
firmware, the KeK-signed dbx update accepted at SetVariable, probe and
cross-build controls green); `qemu_boot_parity_diff.sh` PASS (the demo
chain line-identical between the two firmware paths modulo the
documented environment-fact allowlist).

## The gate's session machine, generalized (2026-11-06, commit `a661b0a`): sessions live in
## the scheduler, recorded at every ring-3 task switch and withdrawn at ring 0

The first slice recorded a session ONCE, at the proof's equip path, because
that caller never returns. The release's machine is not that: a session is
recorded when a task ENTERS ring 3 and forgotten when it resumes in ring 0,
on every switch — including the preemptive ones, which do not pass through
any syscall at all. That is now the tree's shape.

The store moved where the machine actually lives: `experimental::scheduler`
owns it (`NO_SESSION = usize::MAX`, `attach_session`, `withdraw_session ->
Option<usize>`, `session_identity()`), and the recording points are the
scheduler's own ring-3 transitions — `ring3_scheduler`'s cooperative
dispatches and its preemptive switch — with the withdraw at every ring-0
resume (`finale`, `preempt_finale`), where the returned id is asserted
against the one that was attached (a mismatched withdraw is a machine bug
being reported by the machine, not a silent leak). The proof's session id
stays 7, and `ring3_scheduler`'s own POC tasks now take ids from
`POC_SESSION_BASE = 100`, one clear of the house task ids 0..6, so the two
id spaces cannot collide in a boot that runs both.

`service84::attach_gate_session` is now a delegating shell over the
scheduler's API (docs say so, so the next slice removes a call, not a
machine), and the three capability rows plus IPC read
`scheduler::session_identity()` directly. The self-test
`self_test_session_contract` proves attach/withdraw/withdraw-again at the
kernel's own boundary, hooked in `src/lib.rs` after the terminal check.

The IPC rows are SESSIONIZED here, which is what makes the next increment
provable: `ipc::send` tags a message with the SENDING session's id (the
arbiter compares `msg.sender` against `scheduler::session_identity()`, not
against the running task), so the proof's own SEND reaches it as
`0x700000007` even though the message travels through the house mailbox.
Normal boots attach no session, so every ring-0 caller keeps task 0's
identity and the transcripts are untouched.

Checks: `./build.sh` PASS (ledger gate + trust walk); `svc84_proof_test.sh`
PASS (verdict unchanged except the sender is now machine-recorded:
`recv 0x700000007`, `caps recv 0x700000006`); `qemu_boot_test.sh` PASS
(Ready; the C/D/E blocking-receive tape pinned and matched). The UEFI,
Microsoft-secure-boot and BIOS/UEFI parity gates were NOT rerun for this
increment: it touches no boot path, the stub, or the carrier — the boot
transcript is the evidence, and re-running the ~30-minute secboot walk
would re-prove the firmware's carrier, not this change.

## The release's BLOCKING receive, served (2026-11-06, commit `3d65e3d`): 0x46's
## blocked-wait dial answers its instant half and refuses its parked half by name

The 0x46 row carries two dials and always did: `a_xfer == 0` is the plain
drain this tree has served since the IPC increment, `a_xfer == 1` is the
release's BLOCKING receive, refused `-ENOSYS BY NAME` from the surface's
first version. The refusal is now gone and replaced by the honest half of
the release's answer.

Served: when a message WAS on the endpoint, the drain hands it back exactly
as the plain dial does, with the release's ordering — shape (`a2` payload
pointer, `a_rights`), then the budget (`a1`: wider than u32 -> ERANGE, zero
-> EINVAL, both before the destination is looked at), then the destination
(a dead endpoint -> ENOENT, refused before any receive so the caller learns
where the fault was instead of "nothing came").

Refused by name: the BLOCKED half — budget remains, endpoint empty — is
`EAGAIN`, never `ENODATA`. "Waited and got nothing" must not masquerade as
"checked and it was empty"; that distinction is the whole reason the
manifest parks the saved-trap session machine. The sharp edge is stated in
the arm: it never parks on ANY caller. A session's park has no waker — a
session id is never a schedulable task and `unblock_task` keys on the house
scheduler's task ids — so parking a ring-3 caller on its session would hang
it forever. EAGAIN is therefore a RELEASED answer (come back and drain
again), and sessions-as-schedulables is the step that would make the wait
meaningful.

Provable because of the sessionization above: the proof's SEND reaches the
endpoint as session 7, so step 9's legs pin both sides of the instant
answer. `prog_svc84.s` gains 9a SEND(dest 0, seq 42) -> ack 0, 9b blocking
RECV -> `0x70000002a` on BOTH sides (the kernel's `BLOCKED_RECVD_VALUE`
against the program's stored copy), 9c the empty endpoint's `-EAGAIN`, 9d
the dead destination's `-ENOENT`. The capability payload buffer moved
BUF+192 -> BUF+352 (tail zeroed first; the read-back's `held_first8`
compares there), `PROOF_DONE` grew 22 -> 26 qwords with its user-range
check widened to 208, and the verdict carries `blocked` /
`want-blocked` / `again` / `baddest` inside the ipc parens. The kernel
self-test replaces its stale ENOSYS row with the dial's three deterministic
refusals (ENOENT, EINVAL, ERANGE); the queue-state-dependent EAGAIN is
owned by the ring-3 legs, where the queue is provably empty after the
programmable drain. No new call numbers: the ledger gate's shape is
unchanged at ten served calls (0x40..0x49), all exercised.

Checks: `./build.sh` PASS; `svc84_proof_test.sh` PASS with `ipc round trip
(send-ack 0x0 , recv 0x700000007 , want 0x700000007 , shape
18446744073709551594 , empty 18446744073709551555 , blocked 0x70000002a ,
want-blocked 0x70000002a , again 18446744073709551605 , baddest
18446744073709551614)` and `IPC matches`; `qemu_boot_test.sh` PASS (Ready,
the C/D/E blocking-receive tape pinned and matched). UEFI, secboot and
parity gates NOT rerun, for the same reason as the increment above.

## The long gates, rerun on the blocking-receive tree (2026-11-06, at `d319ef6`): the boot-path
## gates were stale by two commits and are green again

The blocking-receive increment (`3d65e3d`) and the session generalization
(`a661b0a`) had only been checked by the three quick gates. The three long
ones were rerun against that exact tree, in this order, and all passed:

* `qemu_uefi_boot_test.sh` PASS -- OVMF booted the stub, the kernel reached
  `BootPhase::Ready`, the GOP framebuffer was handed over and bound, the
  shell rendered, and the C/D/E blocking-receive tape is pinned and matched.
* `qemu_boot_parity_diff.sh` PASS -- the BIOS and UEFI demo chains are
  line-identical modulo the documented environment-fact allowlist.
* `qemu_uefi_ms_secboot_test.sh` PASS -- the enforcing OVMF accepted the
  Authenticode-signed stub chained to the enrolled carrier root, refused the
  unsigned and one-byte-tampered stubs, refused the leaf-signed stub under a
  hash-typed dbx revocation AND under a whole-certificate one, accepted a
  KeK-signed `EFI_VARIABLE_AUTHENTICATION_2` dbx update at SetVariable time
  (read back byte-for-byte) while refusing its non-KeK-signed twin, and the
  kernel reached Ready; the whole-cert dbx shape probe read 1, i.e. the stub
  saw the composed entry byte-for-byte.

No code changed for this: the point was to retire "not rerun" as an open
question on that pair, not to fix anything.

## Sessions as schedulables (2026-11-06, commit `ba94b49`): the blocked receive
## parks on its task instead of refusing, when the caller has a home

The blocked half of the blocking receive refused `EAGAIN` for every caller,
with a stated reason: a session id is not a schedulable, the dispatcher keys
on the TCB table, and a park on an id it has never heard of has no waker.
That reason is now only half true, and the half that is false is the useful
one.

`scheduler` records the session's HOME: `attach_session_for(sid, task)`
stores the session and the house task it is schedulable on, `session_task()`
reads it back, `withdraw_session()` clears the home WITH the id (a withdrawn
session can never answer "parkable"), and `session_is_schedulable()` answers
the only question a parking row may ask -- is the home a real task, and is it
the one running? Task 0 is deliberately NOT a home: it is the kernel's boot
context, and parking it would park the core.

The gate's blocking RECV then delegates to `ipc::receive` for a caller with a
home: the task's TCB goes RUNNING -> BLOCKED inside the trap (the trap frame
stays on its kernel stack; the resume returns into the same arm), the next
`ipc::send` moves it BLOCKED -> READY with the message waiting, and `a1` is
read as the primitive's EMPTY-WAKE budget -- an exhausted budget is the typed
`ReceiveTimedOut`, answered here as `EAGAIN` ("waited, woke, still nothing"),
never `ENODATA`. Callers with no home keep the released `EAGAIN` by name.

The honest scope: the ring-3 proof's caller and the kernel self-test run on
task 0, and the ring3-scheduler POC's sessions live in their own machine, so
the PARK ITSELF is exercised through `ipc::receive` (the C/D/E tape proves it
every boot) rather than from a gate call. Hosting the gate on a house task is
the step that would close that gap, and it is what "sessions as schedulables"
still owes.

Checks: `./build.sh` PASS; `svc84_proof_test.sh` PASS (ring-3 verdict
unchanged -- its caller has no home and keeps its EAGAIN legs);
`qemu_boot_test.sh` PASS with the contract line extended: "a session attached
for task 1 names that home while task 0's names none and is not schedulable,
and withdrawing clears the home -- OK".

## The release's payload dials, served (2026-11-06, commit `f01d867`): messages
## carry bytes, copied into the kernel at the gate and drained by count

`a2 != 0` on 0x45/0x46 was `EINVAL` since the rows landed, and the docs said
why: the primitive modelled a message as `(sender, seq)` only, so accepting a
buffer claim would answer a message the other end never receives. The
primitive carries the bytes now, so the claim is served.

`IpcMessage` gains `payload: [u8; 64]` + `len`; 64 is the release's own
message size (`buflen >= 64`) and the same bound `caps::MAX_PAYLOAD` uses.
Bytes are copied INTO THE KERNEL at send time (a queue entry never holds a
user pointer), `payload()` exposes only the live prefix, and
`with_payload` refuses an oversize body instead of truncating.
`IpcMessage::new(sender, seq)` keeps every pre-existing caller working.

One register selects the dial. SEND: `a2 == 0` is the surface's `(sender,
seq)` form; `a2 != 0` is the release's `SEND(handle, buf, len)` -- `a1` a
user buffer, `a2` its length, `EMSGSIZE` above 64, `EFAULT` when unreadable,
and seq 0 in the body (the release has no seq field; inventing one would be a
number the sender never claimed). RECV: `a2 != 0` is `RECV(idx, buf, buflen)`
-- `buflen` below 64 is `EINVAL` (the release's floor), the destination must
validate WRITABLE, and the answer is the BYTE COUNT with the bytes in the
caller's buffer, on both the instant and the parked path (one `answer`
closure, so the two cannot drift). Transfer tokens and rights stay `EINVAL`:
a message cannot carry a capability yet.

The proof's step 10 pins it end to end -- 8-byte "payload!" SEND (ack 0),
RECV into the release's 64-byte buffer (answer 8), the bytes read back out of
the program's own memory (`0x2164616f6c796170`, so a kernel that answered a
length and copied nothing fails), then the three refusals by name: 65 bytes
`EMSGSIZE`, null pointer `EFAULT`, 32-byte buflen `EINVAL`. Two call-site
bugs the proof caught on its first runs are documented at the site: the
payload RECV legs passed `r10 = -1` (SEND's "no transfer" claim, an unknown
dial on RECV), and the old 6c leg's "payload claim in rdx" had become a
SERVED dial -- the shape refusal it pins moved to an unknown `a_xfer`, and 6d
now clears the register 6c sets.

Checks: `./build.sh` PASS; `svc84_proof_test.sh` PASS with the verdict's ipc
parens carrying `payload (ack 0 , len 8 , bytes 0x2164616f6c796170 , wide
18446744073709551526 , null 18446744073709551602 , short
18446744073709551594)` and `IPC matches`; `qemu_boot_test.sh` PASS (the ring-0
shape rows now pin the payload refusals too). The UEFI, secboot and parity
gates were NOT rerun after this increment: it touches the gate and the
mailbox, neither of which the boot-path gates' kernel-side chain differs on,
and the three were green two commits earlier (above).

## The 0x46 blocking dial parked on a real house task (2026-11-06): a session
## store that is per-task, and the .data bug that proved it

Since the blocking dial landed (`ba94b49`) the parked leg had only ever been
exercised from the ring-3 proof, whose callers are task 0. Doing it from a
scheduler task was refused `EAGAIN` because a task with no session is not a
schedulable home -- honest, but it meant the one dial this release serves
(park and be woken by the next send) had never run on the house scheduler.

That needed the session store to tell tasks apart. Its first cut was a
single global `AtomicUsize`: one session for the whole kernel. The new
gate-park proof killed it immediately -- the moment task 7 attached a
session and blocked, task A's sends started answering `NotRunningTask`,
because the arbiter's sender check compares against `session_identity()`
and every task now read back task 7's session. A global identity can only
be right while exactly one task runs.

The store is `SESSIONS: [AtomicUsize; 32]`, one slot per task, with
`session_of(task)` / `detach_session_for(task)` alongside `attach_session_for`,
`session_task`, `session_is_schedulable`, `withdraw_session` and
`session_identity`. That fix exposed a second bug the proof caught too: the
slots were initialised to `NO_SESSION` (`usize::MAX`), which puts the table
in `.data`, and this boot path does not materialise that section -- every
slot read back whatever the loader left there, and task A's sends declared
identity `6182160`. Slots now store `session id + 1` with 0 meaning "no
session", so the table is zero-initialised and lives in `.bss` like every
other static here. Both bugs are documented at the store, not just fixed.

A third bug was the new self-test's own: its per-task isolation check
records a session FOR another task (to prove the store is per-task), and it
did not put that slot back -- so demo task A woke up owning a session it
never attached, which is exactly the `NotRunningTask` noise the boot log
showed. The test now snapshots the slot, restores it exactly, and asserts
`session_of` agrees.

The proof itself spawns task 7, has it attach session 200 with HOME = this
task, and calls `service84::dispatch(CALL_IPC_RECV, GATE_PARK_EP, 1, ...)`
against an empty endpoint whose owner (task J) is terminated and will
never read it. The gate parks it (state BLOCKED), the next `ipc::send`
from the main task wakes it, and the gate answers the queued message.

Verified transcript lines (`tools/qemu_boot_test.sh`, passing):

```
  experimental scheduler session record/withdraw contract: attach names the recorded id, withdraw reverts and reports it, double-withdraw reports none, identity back to the running task, a session recorded for ANOTHER task leaves this one's identity alone (and is put back exactly as found), the current task's own record names it as the home while task 0 is never schedulable, and withdrawing clears the home -- OK
  service-0x84 gate park: spawned task 7 to call the blocking dial from a caller that HAS a schedulable session home
  service-0x84 gate park: task 7 attached session 200 with HOME = this task; calling 0x46 blocking on the EMPTY endpoint 3 (budget 1)
  service-0x84 gate park: the parked task woke (its session home withdrawn) and carries the message the sender queued -- OK
  service-0x84 gate park self-test: the 0x46 blocking dial PARKED a session-backed task (state BLOCKED), the next ipc::send woke it, and the gate answered the queued message (0x5b , want 0x5b , done true) -- OK
```

Checks: `./build.sh` PASS; `qemu_boot_test.sh` PASS with the pinned C/D/E
tape intact and task A/B's six round trips unchanged; `svc84_proof_test.sh`
PASS with a byte-identical verdict (the proof's callers are still task 0, so
its `EAGAIN` legs keep their answers). The long gates are rerun on this
tree's final HEAD, not here -- see the increment that follows.

## The release's CALL/REPLY pair, served (2026-11-06): the Reply kind is a
## one-shot right to answer, minted by the kernel and consumed by its answer

With the gate parked on a real house task (previous increment), the third
capability kind this tree can honestly carry is the release's `Reply` -- the
one-shot authority to answer ONE call. It needs no device and no shared
region, only the endpoint machine that already exists, which is why it is the
next kind rather than FrameBuffer/SharedMem/BlockDevice/IoPort: this tree has
no framebuffer window, no block-device window and no shared-memory region to
attach those capabilities to, so serving them today would be a costume.

`caps::Kind::Reply` is a capability whose `endpoint` field holds the CALLER's
identity and whose rights mask is SEND and nothing else. Only the KERNEL mints
one -- `call_send` (the release's `call_send`) queues the request with a Reply
capability in the message's dedicated transfer field (§5.1), so a caller
cannot choose what rides along -- and the receiver gets it exactly like any
other transfer: installed into its own table, handle reported by the drain.

Serving it from user code needed one honest change to an existing row:
`CAP_RECV`'s `r8` is no longer "must be zero", it is the OUT-POINTER where
the kernel writes the transferred capability's handle (`u64::MAX` = nothing
transferred, 0 = not wanted). Without it the pair is unusable in ring 3 -- the
capability would arrive in the caller's table and the caller could never learn
its index. That is the release's second return register, expressed the way
`INPUT_POLL`'s record pointer already is.

`reply` answers once: authority-first (`reply_target` -- WrongType unless the
handle is a Reply capability, NoRight without SEND), then the payload bound,
then the caller's ANSWER SLOT (the first live endpoint the caller owns) and
its free room are checked BEFORE the handle is emptied. The release drops the
capability first and only then looks for a wait slot; doing it the other way
round means an answer that cannot land is a refusal the caller can still
retry, not a consumed right with the bytes on the floor. That ordering
difference is documented at the method.

The proof's step 11 is the whole round trip on the session's own endpoint 0:
call (owner 7) → recv (8 bytes, `0x700000008`) with the handle written to the
program's out-slot → the program reads it back (handle 3, out of its OWN
memory) → reply (ack 0) → the SAME handle again (`-EBADF`, one-shot) → an
endpoint handle asked to answer (`-EACCES`, wrong kind) → recv the answer
(8 bytes, `answer!!` read back out of the program's buffer).

Verified transcript lines (`tools/svc84_proof_test.sh`, passing):

```
REPLY (call 0x7 , recv 0x700000008 , handle 0x3 , reply 0x0 , twice 0xfffffffffffffff7 , kind 0xfffffffffffffff3 , answer 0x700000008 , bytes 0x2121726577736e61) , REPLY matches
OK: one call carried a one-shot reply capability round-trip through the session's OWN table (handle 3), the answer landed back at the caller's endpoint, and the second reply and the wrong-kind reply were refused -EBADF/-EACCES by name
```

The kernel-side self-test gained nine clause groups on the same machine
(`experimental caps: 36 clause groups ...`), including the two a single-session
proof cannot show from ring 3: a reply capability cannot be transferred on (it
holds no GRANT, so the transfer clause refuses NoRight) and a call through a
NON-endpoint handle is WrongType rather than a send with a right attached.

**Three proof bugs, all in the checks rather than the machine, found by
running the gates.** The first run of the new legs refused everything: RECV's
transfer field was set to 0 (the plain-drain dial of the *mailbox* rows) where
the capability rows require the "no transfer claim" sentinel `u64::MAX`. The
second run passed the new leg but failed the older CAP leg, because that leg
judged itself against two "last drain" statics that the CALL/REPLY legs then
overwrote -- its expected value had silently become the answer's; it now
derives its expectation from the live session identity and the sentinel
instead. The third was the verdict pin itself, written in decimal for values
the row prints in hex. Each is documented at the site.

Checks: `./build.sh` PASS (the ledger gate now requires 0x4a/0x4b to be
exercised, and `prog_svc84.s` step 11 does); `svc84_proof_test.sh` PASS with
every earlier pin intact; `qemu_boot_test.sh` PASS with the gate-park and
session-contract lines unchanged.

## The long gates, rerun on the call/reply tree (2026-11-06, at `ac34c8c`):
## all three green, and they were green two increments earlier too

The three gates that need their own build (`./build.sh` does not cover them)
were rerun on this round's final HEAD, in sequence, with the tree untouched
while they ran:

```
UEFI_EXIT=0
PASS: OVMF booted the UEFI stub; kernel reached BootPhase::Ready and exited
      cleanly; the GOP framebuffer was handed over, bound, and the shell
      rendered into it
PARITY_EXIT=0
PASS: BIOS and UEFI kernels' demo chains are line-identical modulo the
      documented environment-fact allowlist
      (BIOS 474 chain lines == UEFI 474 chain lines; allowlist deltas only)
SECBOOT_EXIT=0
PASS: ms OVMF under enforcement accepts the Authenticode-signed stub whose
      signer is chained to the enrolled carrier root, refuses both the
      unsigned one and the tampered one, and the two revocation legs
      (hash-typed and whole-cert) are refused by firmware; the stub never ran,
      so the evidence is the composed signature lists (92-byte
      EFI_CERT_X509_SHA256, 932-byte EFI_CERT_X509)
      firmware under test: uefi/tools/OVMF_CODE_4M.ms.fd
      dbx shape probe: 1
```

Two notes worth keeping. First, the parity gate compares 474 chain lines from
both boot paths, so the whole demo chain this round touched -- the per-task
session store, the gate-park proof, and the CALL/REPLY rows -- is proven
identical under BIOS and UEFI. Second, the secboot gate's `dbx shape probe: 1`
is the part that would fail silently if the composed revocation entries ever
stopped reaching the firmware: it re-reads the 975-byte whole-cert entry the
probe firmware served and confirms the stub saw it byte for byte.

The earlier `nohup` wrapper for this sequence died with the tool call that
started it, before the first gate finished building -- recorded here because
a gate sequence that never runs must not be mistaken for one that passed. The
run above was started as a managed background process and polled to its exit
codes.

## The release's blocking CALL, and a call that crosses two sessions (2026-11-06):
## the caller parks mid-call and a different holder answers it

The previous increment served CALL/REPLY but only as a self-call: the caller
and the server were the same session, so the pair never crossed a table
boundary, and the answer was collected with a plain receive rather than by
waiting. Both halves are closed here.

`CAP_CALL` gained a THIRD dial. `a_xfer == u64::MAX, a_rights == 0` is still the
plain call. An answer buffer and length (`a_xfer != 0`, `a_rights >= 1`) make
it the release's `call` + wait: the gate registers a wait slot in the
capability machine, PARKS the calling task, and returns the answer's byte count
with the bytes in the caller's buffer. A half claim is `EINVAL`, task 0 is
refused `EPERM` (parking the boot's own task would leave nobody to run the
scheduler), and a caller woken without an answer gets `EAGAIN` by name.

The machine half is a wait registry: `call_wait_begin` (one outstanding wait per
caller, `CallPending`/`EAGAIN` on the second), `reply` depositing into a PARKED
caller's slot instead of an endpoint and unblocking its task in the same step,
and `take_answer` consuming the slot. Register-then-park is the order that
cannot lose a wake: a server that answers in between finds the slot already
there.

The proof runs the exchange between two DISTINCT holders: session 201 owns
endpoint 0, session 202 holds a copy of a SEND|GRANT capability for it, and each
runs as its own house task. The caller parks mid-call; the server drains the
request on ITS table (retrying while the queue is bare, since the two race by
construction), answers through the one-shot reply capability, and the caller
resumes with the answer.

One design note about who observes the park: the boot's own task cannot. By the
time the scheduler returns to task 0 the caller has already been answered and
has terminated, so sampling its state there is a race that fails about half the
time (it did on this proof's first run). The SERVER observes it instead: at the
moment it drains the request the caller is necessarily off-CPU -- the caller
only leaves it inside the gate's own `block_current` -- so the server's reading
is the park's proof, and the boot gate pins the line.

Verified transcript lines (`tools/qemu_boot_test.sh`, passing, pinned):

```
  service-0x84 call proof: two sessions equipped -- 201 owns endpoint 0, 202 holds handle 0 for it (distinct tables)
  service-0x84 call proof: caller task 8 (session 202) calls handle 0 of ANOTHER session's endpoint and parks
  service-0x84 call proof: server task 9 (session 201) drained the request (reply handle 1) and answered it (ack 0)
  service-0x84 call proof: the caller WOKE with 8 bytes and the server's answer in its buffer -- OK
  service-0x84 call proof self-test: the caller parked (state BLOCKED), the OTHER session's task drained the call (recv 0xca00000008 , want 0xca00000008) and answered once (ack 0), and the caller resumed with 8 bytes reading 0x6b636162676e6f70 (want 0x6b636162676e6f70) -- OK
```

`0xca00000008` is the two tables meeting in one value: the server's drain packs
the CALLER's session id (0xca = 202) above the request length, and the
orchestrator compares it against the constant it equipped, not against
whatever the kernel happened to print.

The kernel self-test gained six more clause groups (`experimental caps: 42
clause groups`), covering the registry's own half: one wait per caller,
`CallPending` on the second, the answer landing in the wait slot rather than an
endpoint, and the slot gone once consumed.

One honest change to existing rows: the user-range validators on the capability
rows are now RING-3-SCOPED (`from_ring3 && ...`). They exist as the ring-3
boundary check, and a cpl-0 caller is the kernel passing its own buffers -- a
kernel stack address is not USER_ACCESSIBLE and was being refused its own
memory. Nothing a ring-3 caller can do changed.

Checks: `./build.sh` PASS; `qemu_boot_test.sh` PASS with the C/D/E tape, the
gate-park proof and the new call proof all pinned and matched;
`svc84_proof_test.sh` PASS unchanged (its CALL leg is the non-blocking dial).

## The release's SharedMem kind, served (2026-11-06): one page, written by the
## kernel and by ring 3, with the geometry the kernel recorded

`caps::Kind::SharedMem` is a mapping capability: `endpoint` holds the region
id, the rights are the release's READ|WRITE|GRANT, and it has no generation and
no queue because what it authorizes is a MAPPING rather than a message. The
kernel owns the region -- it allocates the frames, maps them USER_ACCESSIBLE
and writes the first word BEFORE the transition to ring 3 -- so what a holder
writes is what the kernel reads.

`CAP_SHM` (0x4c) takes a region handle and a 24-byte record and answers the
kernel's own geometry `{base, len, rights}`. It maps nothing: the mapping
already exists, which is what makes the capability an authority over a mapping
rather than a request for one. A handle that names nothing is `EBADF`, a handle
of another kind is `EACCES` (WrongType), and an unknown region id behind a
handle is `ENOENT` (`BadRegion`).

The proof's step 12 is the two-party half, and it is the reason this kind is
worth serving with no second process: the program reads the geometry out of its
own memory, follows the base the RECORD names (not a constant) to read the
kernel's sentinel `0x0123_4567_89ab_cdef` THROUGH the mapping, writes
`0xfeed_face_dead_beef` at offset 8 through the same address, and the kernel's
`PROOF_DONE` reads that offset out of the region itself. If ring 3 were writing
some page of its own, the kernel would still read zero.

Verified transcript lines (`tools/svc84_proof_test.sh`, passing, pinned):

```
  service-0x84 proof: ... session 7 equip -- endpoint 0 granted to the session's OWN table at handle 1 , INPUT cap at handle 2 , SHARED region 0 at 0x60000030000..0x60000031000 at handle 3 ...
  SHM (ack 0x0 , base 0x60000030000 , len 0x1000 , rights 0xe , sentinel 0x123456789abcdef , wrote 0xfeedfacedeadbeef , forged 0xfffffffffffffff7 , kind 0xfffffffffffffff3) , SHM matches
```

The kernel self-test gained six more groups (`experimental caps: 48 clause
groups`): the region table's own half -- the capability naming its region and
rights, the geometry coming from the kernel's record, WrongType on an endpoint
handle, `BadRegion` for an unknown id on both the lookup and the bootstrap path,
and a wider rights claim being MASKED rather than widening.

Two things this slice deliberately does NOT do, named so their absence is
checkable: a holder cannot ASK for a region (the kernel maps first; the
release's create/map/refcount lifecycle is still staged), and there is no
`MAP` row returning a fresh virtual address per holder -- one region, one
kernel-chosen window, told to whoever holds the capability.

Checks: `./build.sh` PASS (ledger gate requires 0x4c, step 12 exercises it);
`svc84_proof_test.sh` PASS with the SHM leg pinned and every earlier pin intact
(the reply capability's handle moved from 3 to 4 because the region occupies
slot 3 -- the pin was updated to say so); `qemu_boot_test.sh` PASS.

## The long gates, rerun on the three-kinds tree (2026-11-06, at `9e90224`):
## green again, and the chain grew by the lines this round added

The UEFI boot, BIOS/UEFI parity and MS secure-boot gates were rerun once more
on the final HEAD of this round -- the blocking CALL, the cross-session proof
and the SharedMem kind all change the boot chain, so the run on `ac34c8c` was
stale the moment they landed:

```
UEFI_EXIT=0
PARITY_EXIT=0
PASS: BIOS and UEFI kernels' demo chains are line-identical modulo the
      documented environment-fact allowlist
      (BIOS 526 chain lines == UEFI 526 chain lines; allowlist deltas only)
SECBOOT_EXIT=0
PASS: ms OVMF under enforcement accepts the Authenticode-signed stub whose
      signer is chained to the enrolled carrier root, refuses both the
      unsigned one and the one-byte-tampered signed one, refuses the two
      revocation legs by firmware, and the stub never ran on any of them
      firmware under test: uefi/tools/OVMF_CODE_4M.ms.fd
      dbx shape probe: 1
```

The parity line count is the useful number: 526 chain lines on both boot paths
(474 before this round), so the two new boot proofs -- the parked caller and the
two-session call, and the shared region -- produce identical transcripts under
BIOS and UEFI rather than merely passing on one.

## Two ring-3 processes, one parked call, one counted region (2026-11-06):
## the scheduler's per-task trap stack, CAP_EXIT, and the region's lifetime

The previous section's call proof had the kernel on one side. That is a true
statement about the machine and a false one about a SERVICE: no program ever
parked on a call, and no two capability tables ever met. This slice makes the
ring-3 proof two PROCESSES -- each its own scheduler task, its own session, its
own trap stack -- and gives the SharedMem kind the release's lifetime.

**Per-task trap stacks (`scheduler::TaskControlBlock::rsp0`).** `TSS.RSP0` is
one machine-wide register, but a trap from ring 3 pushes its frame on the
RUNNING task's kernel stack. With one ring-3 task the two are the same thing
and nobody notices; with two, the second task's trap would land on the first
task's stack -- exactly where the first task's PARKED trap frame lives. So the
TCB records each task's trap-stack top, `yield_task` writes it into the TSS
before every switch to a task that has one, and task 0 (the coordinator) keeps
the boot's own value. Shared RSP0 across parked ring-3 tasks is a silent stack
smash, and it is the reason this could not be done before.

**A ring-3 caller parks on its call.** The blocking dial already existed; what
was missing was a caller that COULD park. `svc84_proof::run` now spawns the
proof's tasks instead of entering ring 3 from task 0, and task 0 becomes the
COORDINATOR: it spawns, pumps round-robin until both processes are
TERMINATED, and only then reads the results back -- so nothing it observes is
a process still running. A ring-3 caller parks inside its trap; the switch
saves that stack pointer, and the resume returns through the same trap and
`iretq`. Nothing touches the parked task's kernel stack in between, which is
why this is a park of a TASK and not a re-entry.

**Two processes, two directions, two wakes.** The rendezvous uses both
primitives, each waking the other out of a park:

1. process A blocks on a receive at mailbox 0 and goes off-CPU; B's SEND
   answers it (the value it wakes with is B's message, recorded in
   `WOKEN_RECVD_VALUE` -- distinct from the instant drain's record, so "the
   park happened" is a value and not an assertion);
2. A runs the rest of its capability steps, sends "go" to mailbox 1, then
   calls B's endpoint and parks;
3. B, which parked on mailbox 1, wakes, drains the call, writes the region,
   answers, and EXITs;
4. A resumes with B's bytes in its own buffer.

**The region's lifetime.** `RegionEntry` counts live CAPABILITIES (not
holders), `CAP_SHM_CLOSE` (0x4d) gives one back, and the record is freed
exactly when the count reaches zero -- so a close by one of three capabilities
is not a teardown. `CAP_EXIT` (0x4e) is the other way a count moves: it
releases the caller's whole table (region counts included), withdraws the
session identity, and terminates the task through the scheduler's own
lifecycle. A holder that never closes anything still cannot pin a dead region
alive. `region_live`/`region_holders` are the read-backs the proof judges:
after both processes are gone the record is gone and names nothing.

**What the verdict says now** (`tools/svc84_proof_test.sh`, passing, pinned):

```
TWO-PROCESS (close 0x0 , geometry 0x0 , call 0x8 , request 0x21416d6f7266 ,
  answer 0x21426d6f7266 , offset8 0xfeedfacedeadbeef , offset16
  0xbeefcafedead1234 , last 0x0 , twice 0xfffffffffffffff7 , region live no
  holders Some(0) , exits 2 session 7 released 1) , PEER (ack 0x0 , base
  0x60000030000 , read 0xfeedfacedeadbeef , recv 0x700000008 , handle 0x2 ,
  request 0x21416d6f7266 , reply 0x0 , wrote 0xbeefcafedead1234) ,
  TWO-PROCESS matches , PEER matches
```

`0x21416d6f7266` is "fromA!", `0x21426d6f7266` is "fromB!"; the region's
offset 8 is read by the PEER through its own capability and offset 16 by the
first process through its own, so the sharing is checked in both directions
with values only the other process could have written.

Named sharp edges, all of them found by running it:

- `scheduler::init()` had NOT run at the point this proof boots, so `spawn`
  returned ids 0 and 1 for a table with no task 0 -- the first process WAS
  task 0 and its park was refused `EPERM`. The proof now initialises the
  scheduler itself (it never returns to the code that would).
- caps RECV's `a0` is an ENDPOINT ID, not a handle: the peer passed 0 and was
  refused `-EACCES` by name, because endpoint 0 belongs to the other process.
  The refusal said exactly that.
- mailbox 1 did not exist (`NoSuchMailbox`): `ipc::prepare_endpoint0_for_proof`
  grew a sibling that creates any endpoint the proof needs.
- `create_process(9000)` PADDS the table up to the id, and the self-test's
  first lifetime section asked for 9000 -- 288 KB that exhausted the kernel
  heap and panicked the boot. Small ids, and the panic is why the row is
  written that way.
- Two ring-3 callers now write the "last drain" statics, so the read-back's
  compares would depend on WHO spoke last: `WOKEN_RECVD_VALUE` keeps the FIRST
  parked wake and `CAP_XFER_HANDLE` keeps the FIRST transfer, both documented
  as such, and the identity the capability legs are judged against is the
  proof's own first session rather than a live `session_identity()` call on
  the coordinator (which would answer task 0).

Deliberately NOT done, named so the gaps stay visible: the two processes share
ONE address space (this tree still has no per-process address space; what is
proven is two tasks, two sessions, two capability tables and one page), a
region still cannot be ASKED for (the kernel maps first), and the pages of a
freed region are not unmapped -- the RECORD dies, the mapping stays.

Checks: `./build.sh` PASS (the ledger gate now reads both programs AND the
coordinator, and requires every served call including 0x4d/0x4e); 
`svc84_proof_test.sh` PASS with the two-process leg pinned; `qemu_boot_test.sh`
PASS with `experimental caps: 60 clause groups` (twelve new clauses cover the
lifetime: counts per capability, survives-a-close, double close, wrong kind,
last holder frees by name, a freed region cannot be re-granted, exit releases
each capability, a dead holder's table stays dead).

## The region a holder ASKS for, and the pages a dead region gives back (2026-11-07):
## `CAP_SHM_MAP` (0x4f), owned regions, and a stack bug the row exposed

Every SharedMem slice before this one named a region the KERNEL had already
mapped: the capability was an authority over a mapping, never a request for
one. This increment serves the release's other direction -- `shm`'s
create/map -- and closes the lifetime at the other end: a region created that
way is owned by the region machine, and when its LAST capability goes away
(close or exit) its pages are unmapped and returned to the frame allocator.

**The row** (`service84::CALL_CAP_SHM_MAP`, 0x4f): `a0` = length in bytes (a
multiple of 4096, at most 16 pages), `a1` = a 32-byte record the kernel writes
`{u64 base, u64 len, u64 rights, u64 handle}`. `a2`, `a_xfer` and `a_rights`
must claim nothing (`EINVAL` otherwise) -- the rights are the kernel half's to
grant, and a caller that could ask for GRANT while the machine's owner is the
kernel would be widening by request. A length that is zero, unaligned or wider
than the window stride is `EINVAL`; a bad record pointer is `EFAULT`; a
refused mapping is `ENOMEM`; a boot with no mapping source installed is
`ENOSYS`. The record carries the HANDLE as well as the geometry, so the row
that answers "where is it" (0x4c) is usable immediately -- the program never
holds a constant address.

**The machine** (`experimental::caps`): `RegionEntry` gained `owned`, and
`CapSystem` gained the two kernel halves as hooks (`unsafe fn(u64) ->
Option<Region>` and `unsafe fn(Region)`), installed together because a mapper
without a releaser would leak every region its holders close. `request_region`
asks the mapper FIRST, records the geometry, mints the capability, and -- the
ordering that matters -- gives the mapping back if the mint is refused, so a
request that answers an error leaves no frames that nothing names. On death
the record is marked dead and the releaser fires ONCE PER REGION, not once per
capability: a holder with two capabilities over one region releases one
mapping, and the self-test proves that with an exit (a count of 2, one
release). The boot's kernel-mapped regions stay `owned: false` and are never
unmapped here -- the 0x84 proof keeps reading its SHM page after its holders
are gone, so releasing that one would be the bug, and the self-test pins the
non-call.

**The kernel half** (`svc84_proof`): requests are mapped at
`0x60000100000` (the proof space's own private PML4 index, `CODE + 0x10_0000`),
one 64 KiB slot per request, eight slots. `map_range` backs each page with a
fresh frame, the region is zero-filled before a holder can read it, and a
partial mapping is unwound and given back rather than answered. The window's
page-table LEVELS are materialised before the frame baseline is taken, so the
balance check measures the region's own pages and not the cost of creating the
tables. The releaser reads the holder's first word out of the mapping BEFORE
unmapping it -- the same two-party evidence the kernel-mapped SHM leg carries,
recorded at the moment the region dies -- then unmaps and frees every page.

**What the proof shows** (`prog_svc84.s` step 15, verified transcript below):
the ring-3 process asks for one page, receives a base it never chose, writes
`0xdeadbeefcafebabe` through it and reads it back; asks for a SECOND region
(two pages) and is given the NEXT window; reads the first region's geometry
back through the handle the record named (0x4c); closes both (0x4d, each the
last capability); and is refused `-EBADF` through the consumed handle. The
kernel's read-back checks, from its own record: both windows UNMAPPED in the
page tables, the releaser's two records (`0xdeadbeefcafebabe` out of the page
the holder wrote, `0x0` out of the one it never touched -- a request is
zero-filled), the two bases, and the frame count back to its pre-pump
baseline. The kernel self-test gained ten clause groups (`experimental caps:
70 clause groups`): no mapper is `NoRegionMapper`, a request's geometry and
rights, the last-close release, a double close that does not release twice, a
refused mint that gives its mapping back and leaves no record, an exit that
releases one mapping for two capabilities, and a kernel-mapped region whose
death does NOT call the releaser.

**A real bug this row exposed, found by running it.** Adding the row inline in
`dispatch` made the ORDINARY boot's parked-call proof cross a guard page: the
caller task calls the gate from a five-page kernel stack, and `dispatch`'s
frame is shared by every arm -- measured at `sub $0x2f18`, ~11.6 KiB, because
the `PROOF_DONE` arm's nine user-memory read-backs and its ~80-argument
verdict print live in it. QEMU's `-d int` log named the fault exactly
(`v=0e e=0002 ... CR2=0000014000023fb8`, a write into the guard page) and the
guard did what it exists to do: a clean double fault instead of corruption.
Fixed at the cause, not by shrinking the demo: the verdict read-back and the
new row are both `#[inline(never)]` bodies, so `dispatch`'s frame is back to
`sub $0xaa8` (~2.7 KiB) -- SMALLER than before this increment -- and a row's
own buffers cost stack only when that row is called.

Verified transcript lines (`tools/svc84_proof_test.sh`, passing):

```
  service-0x84 proof: region REQUESTS armed -- window 0x60000100000 (+0x10000 per request, 8 slots), 296 frames in use before the pump
  ... , MAP (ack 0x0 , base 0x60000100000 , len 0x1000 , rights 0xe , handle 0x3 , wrote 0xdeadbeefcafebabe , again 0x0 , base2 0x60000110000 , len2 0x2000 , handle2 0x4 , close 0x0 , close2 0x0 , stale 0xfffffffffffffff7 , geometry 0x0 , geom base 0x60000100000 , released 2 word 0xdeadbeefcafebabe/0x0 , bases 0x60000100000/0x60000110000 , frames 296 == 296 , unmapped true / true) , MAP matches -- OK
```

**Negative control on the page release** (temporary, reverted immediately
after -- not shipped): `deallocate_frame` skipped in the releaser, so the pages
were unmapped but never returned. The check caught it by name and the proof
refused to pass:

```
  ... MAP ( ... , frames 299 == 296 , unmapped true / true) , MAP MISMATCH -- FAILED
  FAIL: the requested-region leg's mapping, kernel-chosen window or page release failed their read-back
  FAIL: the kernel reported the proof as FAILED
```

Named sharp edges, so their absence stays checkable: the two processes still
share ONE address space (this tree has no per-process space in the proof), so
"a fresh VA per HOLDER" is not claimed -- the window is fresh per REQUEST, and
two holders of one region read the same VA; a requested region is not
distributed to a second holder through the transfer field (the capability
carries GRANT, but no leg exercises that path); and a live holder can never
make the machine reclaim its pages -- the lifetime is the LAST capability, not
a revocation.

Checks: `./build.sh` PASS (the ledger gate requires 0x4f and step 15 exercises
it; `serve_proof_done`/`serve_region_request` keep every arm out of the
dispatcher's frame); `svc84_proof_test.sh` PASS with the MAP leg pinned;
`qemu_boot_test.sh` PASS with `experimental caps: 70 clause groups` and the
parked-call proof's pinned lines unchanged.

## The long gates, rerun on the region-request tree (2026-11-07):
## all three green, on the final HEAD, with the tree untouched

The UEFI boot, BIOS/UEFI parity and MS secure-boot gates were rerun on the
final tree of this round -- the boot chain's caps self-test line changed
(`60 -> 70` clause groups), so the earlier runs were stale by the rule these
gates are held to. The first attempt at this sequence overlapped a
comment-only source edit, which is why the runs below were repeated from
scratch on a tree nobody was editing:

```
UEFI_EXIT=0
PASS: OVMF booted the UEFI stub; kernel reached BootPhase::Ready and exited
      cleanly; the GOP framebuffer was handed over, bound, and the shell
      rendered into it; the C/D/E blocking-receive tape is pinned and matched
PARITY_EXIT=0
PASS: BIOS and UEFI kernels' demo chains are line-identical modulo the
      documented environment-fact allowlist
      (BIOS 526 chain lines == UEFI 526 chain lines; allowlist deltas only)
SECBOOT_EXIT=0
PASS: ms OVMF under enforcement accepts the Authenticode-signed stub whose
      signer is chained to the enrolled carrier root, ... and the kernel
      reached Ready
      dbx shape probe: 1
```

The parity count is unchanged at 526: this round added no demo-chain lines
(the region request is exercised by the separate `nyrqis.proof=svc84` boot),
only the clause count inside one existing line, which both boot paths print
identically.
## The region a holder HANDS ON (2026-11-08): `CAP_SEND`'s transfer field carries a
## requested region to a second holder, and the queued copy is a holder

The previous increment let a holder ASK the kernel for a region and gave that
region's pages back when its last capability closed. What it could not do is
pass the region on: the transfer field existed on `CAP_SEND` (0x47), and
`CAP_RECV` (0x48) could install a delivered handle for the Reply kind, but a
SharedMem transfer was counted by nobody, so a sender that closed its own
capability after sending would have freed the pages out from under the copy
still sitting in the receiver's mailbox.

**The lifetime rule this increment adopts.** A QUEUED COPY COUNTS AS A HOLDER
from the moment `send` queues it, not from the moment `receive` installs it.
The receiver's install HANDS THE COUNT OVER (it does not add a second), and
every path that discards a queue gives its counts back -- releasing the pages
if the discarded copy was the last holder. Counting at receive time instead
would make the window between queue and drain invisible to the region machine,
which is exactly the window the proof exercises.

**The kernel half** (`experimental::caps`): `send` increments the region's
holder count when it queues a SharedMem capability and refuses a DEAD region
by name (`BadRegion`); `receive` hands the count over on install and falls back
to dropping the orphan count if the install is refused, so a full receiver
table cannot leak a holder; `drop_queued` is the single place a queue gives its
counts back, and it is called by `terminate_process` and `revoke` as well as by
a discarded message. A `live` guard (`holders == 0 && entry.live`) on the
close/release/drop paths means a stale capability over an already-dead region
cannot release a second time. `rights::ALL` (`SEND|GRANT|READ|WRITE`) was added
because 0x47's `a_rights` bound was `SEND|GRANT`, which made every region
transfer asking for READ or WRITE fail as `EBADF` -- the bound was smaller than
the release's own rights vocabulary. `Message` gained `transferred_kind`, and
`CAP_XFER_HANDLE` (0x48) records only `Kind::Reply` transfers: with the region
handover also filling that field, the first-wins assignment would have broken
the CALL/REPLY pin that reads the reply handle from it.

**What the proof shows** (`prog_svc84.s` step 16, `prog_svc84_peer.s` steps
3b-3f; the first ring-3 leg placed BEFORE the parking call because the receiver
must be alive to take the copy). The sender requests a region, writes
`0xfeedfacecafed00d` at offset 0, sends it to the peer through the transfer
field, CLOSES ITS OWN CAPABILITY while the copy is still queued, and reads the
page again (`survived 0xfeedfacecafed00d` -- the mapping outlived the
capability because the queued copy held it). The peer drains the transfer
before the call it answers, reads the sender's marker across the handover,
writes `0x1122334455667788` at offset 8, then closes -- the last holder -- and
that close is what returns the pages. The kernel's releaser reads BOTH words
out of the mapping as it unmaps (`first_word`/`first_word8`), so the release
record contains a byte from each process, and neither program reports it.

Because the handed-on request runs before step 15's two, the window slots are
now: handover `0x60000100000`, step 15 `0x60000110000` and `0x60000120000`.
The release order inverts that for the first leg only -- the receiver closes
before it answers -- so `released 3` with `first_word 0xfeedfacecafed00d` (the
handover, both words) and `last_word 0x0`/`last_word8 0x0` (step 15's own
requests, never written by anyone, which is what keeps the zero-fill claim
checkable).

Verified transcript lines (`tools/svc84_proof_test.sh`, passing):

```
  ... PEER-XFER (recv 0x700000004 , handle 0x2 , base 0x60000100000 , read 0xfeedfacecafed00d , wrote 0x1122334455667788 , close 0x0 , tries 0)
  ... MAP (ack 0x0 , base 0x60000110000 , ... , released 3 word 0xfeedfacecafed00d/0x0 , bases 0x60000100000/0x60000120000 , frames 296 == 296 , unmapped true / true / true , xfer (ack 0x0 , base 0x60000100000 , send 0x0 , close 0x0 , survived 0xfeedfacecafed00d , last8 0x1122334455667788)) , MAP matches -- OK
```

**Negative control on the count** (temporary, reverted immediately after -- the
restored tree is what passed and what is committed): the increment in `send`
removed, so the queued copy was invisible to the region machine. The sender's
close then freed the pages under a copy that had not been drained, and the
proof never reached its verdict line -- the receiver's read faulted in ring 3:

```
  FAIL: the ring-3 proof never reached its verdict line
FAIL: 1 ring-3 service case(s) failed -- see target/svc84-proof/
```

Checks: `./build.sh` PASS; `svc84_proof_test.sh` PASS with the PEER-XFER leg
pinned; `qemu_boot_test.sh` PASS with `experimental caps: 85 clause groups`
(fifteen new clauses: a queued copy counted, the sender's close not freeing,
handover on install, a refused install giving the count back, a discarded queue
releasing, a dead region refused by name on send, the double-release guard, and
`rights::ALL`'s own shape).

Named sharp edges: the transfer is a COPY of the capability, so the receiver
holds its own attenuated one and the sender's close is not a revocation; the
two proof processes still share one address space (the handover is proven for
the capability and the region machine, not for per-process page tables); and a
region handed on with `a_rights` wider than the sender held is refused by the
existing attenuation check rather than clipped.

## The long gates, rerun on the handover tree (2026-11-08):
## all three green on the final sources, with the tree untouched during the runs

Rerun because the boot chain's caps self-test line changed (`70 -> 85` clause
groups) and because the proof gained a leg. No source file was edited after the
runs started -- only this document and the port manifest, which no gate reads.

```
UEFI_EXIT=0
PASS: OVMF booted the UEFI stub; kernel reached BootPhase::Ready and exited
      cleanly; the GOP framebuffer was handed over, bound, and the shell
      rendered into it; the C/D/E blocking-receive tape is pinned and matched
PARITY_EXIT=0
PASS: BIOS and UEFI kernels' demo chains are line-identical modulo the
      documented environment-fact allowlist
      (BIOS 526 chain lines == UEFI 526 chain lines; allowlist deltas only)
SECBOOT_EXIT=0
PASS: ms OVMF under enforcement accepts the Authenticode-signed stub whose
      signer is chained to the enrolled carrier root, ... and the kernel
      reached Ready
      dbx shape probe: 1
```

The parity count is unchanged at 526: the handover is exercised by the separate
`nyrqis.proof=svc84` boot, so no demo-chain line was added -- only the clause
count inside one existing line, which both boot paths print identically. The
secure-boot gate still takes four negative boots (unsigned, one-byte-tampered,
hash-revoked, whole-cert-revoked) plus the KeK-signed dbx update, each capped at
240 s; the run above is from a fresh start after its rebuild, not a resumed one.

## Two address spaces for the two proof processes (2026-11-08), and the wider
## ask that attenuation refuses

The previous increment handed a requested region from one process to the
other. Both processes still ran in ONE address space, so "the same virtual
address" meant "the same page" and the peer only worked because it had been
linked at `0x7<<40` -- an anchor it needed *because* there was one table. This
increment removes that crutch and adds the negative control the attenuation
rule was missing.

**Two PML4s, identical virtual addresses.** The scheduler already had what
this needs -- `spawn_with_cr3` names a task's PML4 and `switch_to` writes it on
every switch -- so the task side was free. What changed is the proof's own
layout: the peer program is now linked at `0x60000000000`, the SAME anchor as
the first program, and loaded with its own space ACTIVE (`elf::load` writes
through destination virtual addresses, so loading the peer under the first
program's tables would have filled the first program's frames). Both processes
have code, results page and stack at the same VAs, over different frames.

**The shared page is mirrored, the requested windows too.** The region the two
processes meet in (`PROOF_SHM`) is mapped at the same VA in both spaces over
the SAME frames -- one page, two translations. Requested regions are mirrored
the same way by the region hooks: `allocate_region_mapping` maps through the
requester's (active) tables and then maps the same frames into the other proof
space, all-or-nothing; `release_region_mapping` unmaps from the active space
AND from the other one. That asymmetry was the one bug this increment hit: the
first version unmapped only the caller's space, so the region handed on to the
receiver was released by the RECEIVER's close and left the SENDER's mapping in
place -- `unmapped false / true / true` on the first run, fixed by making the
hook pick the other space (whichever PML4 is not the active one) instead of
assuming it is the peer's.

**What the proof shows.** Each task runs with its own CR3. The kernel reads the
word at `0x60000010000 + 8` on each side of an explicit CR3 switch: on the
first program's side that word is its INPUT_STATE record (`0x1260296`), on the
peer's side it is the region base its OWN geometry call answered
(`0x60000030000`). Same VA, two pages, both values pinned:

```
  ... PEER-XFER (recv 0x700000004 , handle 0x2 , base 0x60000100000 , read 0xfeedfacecafed00d , wrote 0x1122334455667788 , close 0x0 , tries 0) , SPACES (same va 0x60000010000 , first 0x1260296 , peer 0x60000030000) , MAP ( ... , frames 302 == 302 , unmapped true / true / true , xfer ( ... )) , MAP matches -- OK
```

The frame count moved from 296 to 302 and still balances: the peer's PML4, its
page-table levels and its own pages are allocated before the baseline, and
every one of them is still live at the read-back.

**Negative control on the read-back's CR3 switch** (temporary, reverted
immediately after): `select_peer_space()` removed, so the peer's page was read
in the first program's space. The same VA then answered the FIRST program's
word twice, and the proof refused to pass:

```
  SPACES (same va 0x60000010000 , first 0x1260296 , peer 0x1260296)
  FAIL: the two processes' address spaces were not distinct (the same VA read the same page, or the peer's page was not the one it wrote)
  FAIL: the kernel reported the proof as FAILED
```

**The wider ask, refused by name.** `rights::ALL` bounded 0x47's `a_rights`
one increment ago so a region transfer could ask for READ|WRITE; nothing
pinned what happens when a holder asks for MORE than it holds. Now the ring-3
proof sends the same region twice: once attenuated to READ|WRITE (`0x0c`) and
once claiming ALL four rights (`0x0f`) -- SEND is one the sender never held.
The second is refused `EMSGSIZE` (`0xffffffffffffffa6`), before the queue is
touched, and the receiver still drains the ONE good message at `0x0c`. The
kernel self-test gained the matching clause: a `READ|GRANT` region capability
handed on as `WRITE|GRANT` answers `Widening`, leaves the holder count alone,
leaves no message queued, and the later close still finds the region alive
(`experimental caps: 86 clause groups`).

Named sharp edges: the two spaces are built by the PROOF, so the ordinary boot
still uses one space and no demo task has its own; the region machine's mirror
is a proof-side policy (a region's mapping exists in both proof spaces, chosen
by "the other PML4"), not a per-holder mapping keyed by the holder's identity;
and `user_range_ok` is evaluated against whichever space is active, so the
read-back must switch spaces before it validates the peer's pointer -- the
control above is what keeps that ordering honest.

Checks: `./build.sh` PASS; `svc84_proof_test.sh` PASS with the SPACES and
wider-ask legs pinned; `qemu_boot_test.sh` PASS with `experimental caps: 86
clause groups` and the ordinary boot's pinned lines unchanged (the hooks see no
peer space there and take the early return).

## The long gates and the release discs, on the two-space tree (2026-11-08)

Rerun because the peer's anchor, its address space and the proof's read-back
all changed, and because the caps self-test line grew (`85 -> 86` clause
groups). No source file was edited after the runs started.

```
UEFI_EXIT=0
PASS: OVMF booted the UEFI stub; kernel reached BootPhase::Ready and exited
      cleanly; the GOP framebuffer was handed over, bound, and the shell
      rendered into it; the C/D/E blocking-receive tape is pinned and matched
PARITY_EXIT=0
PASS: BIOS and UEFI kernels' demo chains are line-identical modulo the
      documented environment-fact allowlist
      (BIOS 526 chain lines == UEFI 526 chain lines; allowlist deltas only)
SECBOOT_EXIT=0
PASS: ms OVMF under enforcement accepts the Authenticode-signed stub ... and
      the kernel reached Ready
      dbx shape probe: 1
```

The parity count is still 526: the two-space proof runs under
`nyrqis.proof=svc84`, so no demo-chain line changed -- only the clause count
inside one existing line.

**The release discs** were built from this commit with `make_live_iso.sh` and
verified from the outside with `check_live_iso.sh` (`--boot` and
`--uefi-boot`), which is the repository's attribution gate rather than a
self-report:

```
dist/nyrqis-live.iso         24037376 bytes  sha256 6bef20399328148d17c7ed1cc3aaef0128a2b6de3e057a203284077d2cc3fe65
dist/nyrqis-live-uefi.iso    21348352 bytes  sha256 824ebeefaf7b556fc25b4e7d7d827ae3b635fe9095435053538f8c9e2c44713e
kernel carried by both:      target/kernel.elf  sha256 e1f6421005fdbd074dd499b3dbfe7b2e1510c0ca4cefe55a09e332e1d422936e
```

`check_live_iso.sh` answered all three questions it exists for: the ISOs match
their recorded digests, the kernel inside the BIOS disc is the kernel this tree
built, that disc boots headless to a verified device-mode session, and the UEFI
disc boots through OUR signed stub (carrier walked, GOP handed over, shell
rendered).

## A region's mapping can be REVOKED while a holder still lives (2026-11-08),
## and the holder's own next read is what proves it

Everything before this increment made a region's lifetime depend on its
capabilities: the pages went back when the LAST holder closed. That leaves
the other direction of the release's revocation model unproven -- a holder
that never closes keeps its pages forever, so "a capability is authority
over a page" was only half said: nothing could take the page away.

**The machine half** (`experimental::caps`): `revoke_region` takes a live,
OWNED region's pages back NOW -- the releaser fires once, the record goes
dead, and the capabilities that named it become STALE. Their handles still
resolve (the kernel took the page, not the handle), the geometry row that
reads the kernel's own record answers `BadRegion`, their later closes free
nothing (the `live` guard), and a second revoke answers `BadRegion`. A
region the KERNEL mapped is refused `NotOwned`: it has no releaser and no
frames this machine may take back. This is not a row any capability can
call -- holding a region never gives you the power to take it away.

**The evidence problem, and the probe that solves it.** "The holder's next
read faults" cannot be said from page tables alone, and a ring-3 page fault
used to end the machine (`page_fault_handler` prints and halts). So
`experimental::probe_fault` arms ONE expected fault (address + the task
expected to touch it); the handler checks it matches, records CR2, the error
code, whether the access came from ring 3 and which task made it, then
terminates that task through the scheduler's own lifecycle
(`terminate_current`, the same edge `CAP_EXIT` uses) and disarms. A fault
that does not match still halts exactly as before -- this is a keyhole for
one named fault, not a user-fault policy. The faulted task's capabilities
are released and its session withdrawn on the way out
(`service84::abandon_gate_session`), in the same order `CAP_EXIT` uses, so a
dead holder cannot pin a region by simply not existing any more.

**What the proof shows.** The peer no longer closes the handed-on region
(that close was what used to make it the last holder). It parks on its own
mailbox -- off-CPU, inside a trap, so nobody is touching the page -- and the
coordinator, once the first process is Terminated and the peer is Blocked:
looks the region up by the base the KERNEL handed out, arms the probe for
that base and that task, REVOKES the region, and sends the wake. The peer
resumes at the instruction after its receive and reads the base: the page is
gone, the load faults, the probe records and terminates it, and the kernel
keeps running to print the verdict.

```
  REVOKE (base 0x60000100000 , addr 0x60000100000 , err 0x4 , task 2 , expected 2 , hits 1 , woke 1)
  ... released 3 word 0xdeadbeefcafebabe/0xfeedfacecafed00d , bases 0x60000110000/0x60000100000 , frames 302 == 302 , unmapped true / true / true ... MAP matches -- OK
```

`err 0x4` is the CPU's own word for it: not present (bit 0 clear), a READ
(bit 1 clear), from user mode (bit 2 set). The release order moved with the
change and is now: step 15's two regions when the first process closes them
after its parked call returns, then the handed-on region when the kernel
revokes it -- so the LAST release is the one with both processes' bytes in
it (`0xdeadbeef...` from the first process at offset 0 is the FIRST release
now, and `first8`/`last8` are both pinned). The peer's own exit accounting
changed with it: one process exits through `CAP_EXIT` (`exits 1`, releasing
its region capability count 0), the peer is terminated by the fault, and the
kernel's parked-wake count is 3 (the peer now parks twice: for the GO and
for the revocation).

**Negative control on the revoke** (temporary, reverted immediately after):
the coordinator armed the probe and woke the parked peer but did NOT take
the pages back. The read then succeeded and the peer exited normally --
`hits 0`, no fault, and the check refused to pass:

```
  REVOKE (base 0x60000100000 , addr 0x0 , err 0x0 , task 18446744073709551615 , expected 2 , hits 0 , woke 1)
  FAIL: the revocation leg's page did not fault (or faulted elsewhere, or was not revoked while a holder lived)
```

Named sharp edges: revocation is kernel-initiated (no row exposes it, so a
capability can never revoke another's region); it takes the WHOLE region --
there is no partial/per-page revocation; the probe is a single-shot keyhole
armed by this proof, so a general user-fault policy is still the manifest's
staged item; and the faulted task's cleanup is `abandon_gate_session`, which
is the EXIT row's two steps without its exit statistics.

Checks: `./build.sh` PASS; `svc84_proof_test.sh` PASS with the REVOKE leg
pinned; `qemu_boot_test.sh` PASS with `experimental caps: 90 clause groups`
(four new: revoke with two holders releases once, the holders' handles stay
resolvable while the geometry refuses, their stale closes free nothing, and
a dead region cannot be revoked twice).

## The long gates and the release discs, on the revocation tree (2026-11-08)

Rerun because the region machine gained revocation, the peer no longer closes
(so the proof's release order and exit accounting changed), a page-fault path
was added, and the caps self-test line grew (`86 -> 90`). No source file was
edited after the runs started.

```
UEFI_EXIT=0
PASS: OVMF booted the UEFI stub; kernel reached BootPhase::Ready and exited
      cleanly; the GOP framebuffer was handed over, bound, and the shell
      rendered into it; the C/D/E blocking-receive tape is pinned and matched
PARITY_EXIT=0
PASS: BIOS and UEFI kernels' demo chains are line-identical modulo the
      documented environment-fact allowlist
      (BIOS 526 chain lines == UEFI 526 chain lines; allowlist deltas only)
SECBOOT_EXIT=0
PASS: ms OVMF under enforcement accepts the Authenticode-signed stub ... and
      the kernel reached Ready
      dbx shape probe: 1
```

The parity count is still 526: the whole increment lives under
`nyrqis.proof=svc84` plus four clauses inside one existing self-test line.

The release discs were rebuilt from this commit and verified outside the tree
with `check_live_iso.sh --boot --uefi-boot`:

```
dist/nyrqis-live.iso        sha256 704a43897e8b66704e2e7e5587563190fc5af6e07b2a2980a5760bf44efe1ddb
dist/nyrqis-live-uefi.iso   sha256 7ea31b067dabdc120465d517aea838a98814c84c9cd9943f8442be006d9ad8f9
kernel carried by both:     sha256 6b880d6193d883c010a969f458f40e715e03a4000616b8bbba5cef592051ab1f
```

Both discs match their recorded digests, the BIOS disc carries the kernel this
tree built and boots to a verified device-mode session, and the UEFI disc boots
through our signed stub.

## A ring-3 page fault kills the PROCESS, not the machine (2026-11-08)

`idt::page_fault_handler` had two outcomes: the boot's armed guard-page demo
and "print and halt". Both are ring 0. A ring-3 fault -- a process touching a
page it does not have -- therefore stopped the kernel dead, which made one
buggy program a machine-wide kill and forced the revocation leg to arm a
single expected fault to get its evidence.

The policy is now the ordinary one for a protected machine, and there is no
arming step left to forget (`experimental::user_fault`):

* every fault taken from RING 3 is RECORDED -- CR2, the CPU's error word, the
  task, its session, and a reason derived from the error bits (`not mapped`
  when bit 0 is clear, `protection` otherwise);
* the faulting task's capabilities are released and its session withdrawn
  (`service84::abandon_gate_session`, the EXIT row's two steps), then the task
  is TERMINATED through the scheduler's lifecycle -- the same edge `CAP_EXIT`
  uses -- so a dead holder cannot pin a region by not existing any more;
* the rest of the machine keeps running. A fault from RING 0 still halts: the
  kernel contradicting its own mapping record is not recoverable.

```
  user fault: task 2 (session 8) touched 0x60000100000 (not mapped, a read) -- error 0x4, 2 capability(ies) released, the task is terminated, the kernel keeps running
```

The revocation leg's verdict now pins the derived reason as well as the
address/task, so the record is checked, not just the survival:
`REVOKE (... , err 0x4 , task 2 , ... , reason not mapped , session 8)`.

## A holder drops its own WINDOW while keeping the capability (2026-11-08)

Until now the mapping and the capability travelled together: a close gave the
capability back and left the window until the region died, and a revocation
took the frames from under everybody. A holder could not say "stop mapping
this for me, I am done with the page, but the region is not yours to free".

**The table** (`experimental::caps`): a region entry now records WHICH HOLDERS
have a live window on it (`mapped: Vec<holder>`), separate from the holder
count and from the frames. A window is added when a capability is minted
(the request maps for the requester; the transfer install mirrors into the
receiver's space) and removed when a holder drops it.

**The row**: 0x50 `CAP_SHM_UNMAP`, a0 = a region handle the caller KEEPS.
It unmaps the caller's own space -- one window -- and nothing else: the frames
are not returned (another holder may be reading them), the other holders'
windows are untouched, and the capability still answers geometry. A holder
with no window left is refused `ENOENT` by name rather than told a second
window was dropped. The kernel half is `unmap_region_window` (a new hook,
separate from the releaser because they act on different things).

**What the proof shows** (step 16g-16i, peer step 7b):

```
  WINDOW (req 0x0 , base 0x60000110000 , len 0x1000 , handle 0x3 , marker 0xddba11cafef00d , unmap 0x0 , again 0xfffffffffffffffe , geom 0x0 , geom base 0x60000110000 , peer read 0xddba11cafef00d , no-window release 1)
```

The first process asks for a region, writes a marker, then drops its window
(`unmap 0x0`) and drops it again (`again -ENOENT`); geometry still answers
afterwards. The PEER -- the other address space the kernel mirrored those
frames into -- reads the marker at that window AFTER the drop, from ring 3,
which is the claim the row exists to make. And `no-window release 1` is the
machine's own record: when the region finally died (the first process exited
still holding the capability) the releaser ran in that process's space and
found NO window there.

**Two bugs this leg found, both fixed:** the releaser captured the region's
first words by reading the mapping, which faults when the holder had already
dropped its window (guarded by the machine's own mapping record now); and it
freed a frame only when the LOCAL unmap succeeded, leaking exactly the dropped
window's page (it now finds the frame in whichever space still names it --
`frames 302 == 302`).

**Negative control** (temporary, reverted immediately): the same run with the
unmap hook turned into a no-op. Every pin still matched except the one that
matters -- `no-window release 0`, i.e. the release found the window still
there -- and the check refused to pass:

```
  FAIL: the window leg's per-holder unmap did not hold (the row, its refusal, the capability's survival or the other window)
```

Named limits: the per-holder record is keyed by HOLDER, and in this proof a
holder is one address space, so the row unmaps exactly the caller's space --
two holders sharing one space, and a region mapped at a different VA per
holder, remain staged; the frames still belong to the region, not to a
holder, so this is not a reference count; and 16g's region is released by the
process's exit rather than by a close, which is why the exit's released count
went `0 -> 1`.

Checks: `./build.sh` PASS; `svc84_proof_test.sh` PASS with 19 checks (the
WINDOW leg pinned end to end); `qemu_boot_test.sh` PASS with
`experimental caps: 93 clause groups` (three new: one holder drops its own
window while the other's stays and no frame is released, the second drop is
refused by name, and the frames outlive both windows until the last close);
`check_svc84_ledger.py` PASS -- 17 served calls, `0x40..0x50`, disjoint from
the release's ledger.

## The release discs for the ring-3-fault / per-holder-window tree (2026-11-09)

Both live discs were rebuilt from this commit (`d337255`) with
`./tools/make_live_iso.sh` and verified outside the tree with
`check_live_iso.sh --boot --uefi-boot`:

```
dist/nyrqis-live.iso        sha256 44f204c798c47fd5284884c8cafc86e72636161187d1785dca25bd181346eadc
dist/nyrqis-live-uefi.iso   sha256 66c811d0a741085a39b0be52701f916d8ddbf8dc731d356867efb272ec978162
kernel carried by both:     sha256 c063409ae175717b6f6421ce557d49f34ceeeaf416fe0e8f97e28d99768c1cb1
```

Both discs match their recorded digests, the BIOS disc carries the kernel this
tree just built and boots the default entry to desktop with the framebuffer and
ps2-aux bound and the display verified, and the UEFI disc boots under OVMF
through OUR signed stub (carrier walked, GOP handed over, shell rendered and
verified) rather than GRUB.

The long gates were rerun against this tree as well, from the released
sources:

```
UEFI_EXIT=0
PASS: OVMF booted the UEFI stub; kernel reached BootPhase::Ready and exited
      cleanly; the GOP framebuffer was handed over, bound, and the shell
      rendered into it; the C/D/E blocking-receive tape is pinned and matched
PARITY_EXIT=0
PASS: BIOS and UEFI kernels' demo chains are line-identical modulo the
      documented environment-fact allowlist
      (BIOS 526 chain lines == UEFI 526 chain lines; allowlist deltas only)
SECBOOT_EXIT=0
PASS: ms OVMF under enforcement accepts the Authenticode-signed stub whose
      signer is chained to the enrolled carrier root, refuses both the
      unsigned one and the one-byte-tampered signed one, refuses the
      leaf-signed stub once its signer's To-Be-Signed hash is revoked BY HASH
      in dbx AND once its certificate is revoked in the whole-certificate dbx
      form, ACCEPTS a KeK-signed EFI_VARIABLE_AUTHENTICATION_2 dbx update at
      SetVariable time (read back byte-for-byte) while refusing its
      non-KeK-signed twin, and the kernel reached Ready
      dbx shape probe: 1
```

Named limits: the discs are built from a dirty-set-free source tree but the
UEFI stub is a `dev` build, the ESP is sized to the stub (7 MiB) rather than
padded, and the UEFI disc has no extracted-kernel attribution -- the kernel
lives inside the stub binary, so its attribution is the single build run that
produced both discs from one `target/kernel.elf`.

## The endpoint BADGE and NOTIFY clauses, served through the gate (2026-11-09)

Two of the release's capability clauses that the port manifest still named
as missing are now ported and served. Neither needed a new kernel object:
both are properties of the ENDPOINT capability this machine already had.

**Badges** (`capability.rs`'s `badge`). A holder with GRANT on an unbadged
endpoint capability can mint ONE badged copy in its own table. The badge is
immutable (no re-badge, no un-badge, zero is "unbadged"), and an attenuating
transfer carries it unchanged. Every message sent through the copy reports
it as `Message.badge`, so a server can tell its clients apart without
trusting payload bytes. Served as 0x51 `CAP_BADGE`; a zero badge, another
kind, an empty slot and a re-badge are all refused by name.

**Notify** (NPS-003 §3). The endpoint record gained a payload-free signal
word: `notify` ORs bits in with SEND's exact authority checks, and the
OWNER's `take_notify` returns and CLEARS the word. It never queues, so it
cannot fill the message queue and an empty receive stays empty. Served as
0x52 `CAP_NOTIFY` and 0x53 `CAP_NOTIFY_TAKE`; a non-owner's take, a
wrong-kind handle and an empty slot are refused by name.

```
service-0x84 proof: BADGE/NOTIFY (notify 0x0 , take 0x5 , again 0x0 ,
  wrong-kind 0xfffffffffffffff3 , forged 0xfffffffffffffff7 ,
  non-owner 0xfffffffffffffff3 , badge 0x4 , zero 0xfffffffffffffff3 ,
  kind 0xfffffffffffffff3 , forged-badge 0xfffffffffffffff7 ,
  twice 0xfffffffffffffff3) , BADGE/NOTIFY matches
```

The first run of the leg caught a real ordering defect: the gate refused a
zero badge as a SHAPE error (`-EINVAL`) before the machine's own
`NotOwner` (`-EACCES`) could answer, stealing a by-name clause refusal. The
gate now bounds only the u32 shape and lets the machine own the zero-badge
rule. The kernel self-test's first run likewise panicked `BadProcess` after
the new section created holder id 5, which the later region-REQUEST section
reserves; the clause now uses the existing SERVER/CLIENT holders and its
own endpoint instead of a new id, so `create_process`'s pad-to-id behaviour
cannot collide with a later reservation.

Checks: `./build.sh` PASS (ledger gate: 20 served calls, `0x40..0x53`,
disjoint from the release ledger and exercised by the proof);
`svc84_proof_test.sh` PASS with the BADGE/NOTIFY leg pinned end to end;
`qemu_boot_test.sh` PASS with `experimental caps: 104 clause groups`
(including the new badge mint/carry/transfer/immutability and the notify
word's OR/take/clear/no-queue/non-owner/wrong-kind rules, positive and
negative);
`qemu_boot_parity_diff.sh` PASS (526 chain lines, BIOS == UEFI modulo the
allowlist).

Still staged, named rather than implied: the device/window kinds
(BlockDevice, IoPort, FrameBuffer) and containers (NPS-007).

## The badge reaches ring 3 on CAP_RECV (2026-11-09)

The badge row alone proved the tag was minted and carried, but a server
could not READ it: 0x48 `CAP_RECV` answered `(sender << 32) | len` and the
optional transferred handle, and nothing else. The row now takes a third
optional out-pointer: `a_xfer = u64::MAX` means "no badge wanted" (the same
"none" sentinel SEND uses), and any other value is a u64 where the kernel
writes the drained message's badge (0 = unbadged). The pointer is validated
BEFORE the queue is touched, so a bad badge pointer cannot consume a message
it never delivered -- the same authority/shape-first ordering the input and
capacity rows already use.

The ring-3 leg sends through the badged handle and then calls RECV twice:
once with a NULL badge pointer, which answers `-EFAULT` and leaves the
message queued, and once with a valid pointer, which drains it, copies the
payload, writes `0xbeef`, and answers the packed identity. The kernel
verdict pins all four values:

```
service-0x84 proof: BADGE/NOTIFY (... , send 0x0 , recv 0x700000006 ,
  tag 0xbeef , null-tag 0xfffffffffffffff2) , BADGE/NOTIFY matches
```

Checks after this slice: `./build.sh` PASS; `svc84_proof_test.sh` PASS (the
BADGE/NOTIFY leg now includes the receive-side tag and the pre-drain
refusal); `qemu_boot_test.sh` PASS with `experimental caps: 106 clause
groups` (two more: an unbadged send reports 0, and a SEND-only attenuated
transfer still carries the tag); `qemu_boot_parity_diff.sh` PASS (526 chain
lines, BIOS == UEFI modulo the allowlist).

## The release discs carrying badge/notify (2026-11-09)

Both live discs were rebuilt from this tree with `make_live_iso.sh` after
the badge-on-receive slice and verified outside it with
`check_live_iso.sh --boot --uefi-boot`:

```
dist/nyrqis-live.iso        sha256 56ec6f0531767041ebddef2bb2a95779b6b3e5f75433baed66d8ff988537d82c
dist/nyrqis-live-uefi.iso   sha256 7f49da536a5dc1d3dc449b725a6098ce83436fca7366246108b81342e39dba33
kernel carried by both:     sha256 5ab536d6f9c3e316670978917cb9226c2f94df48b9611d5950fe39721824efe5
```

Integrity and attribution match, the BIOS disc boots the default entry to a
verified desktop session, and the UEFI disc boots through our signed stub
(carrier walked, GOP handed over, shell rendered and verified).

Named limits unchanged: the UEFI stub is a `dev` build, and the UEFI disc
has no extracted-kernel attribution because the kernel lives inside the
stub binary; its attribution is the one build run that produced both discs
from one `target/kernel.elf`.

## The resource kinds and containers (2026-11-09)

The last named capability gap was the device/window kinds and NPS-007
containers. BlockDevice, IoPort and FrameBuffer are now kinds in
`experimental::caps`, each carrying the WINDOW it authorizes -- sectors,
I/O ports, scan-out rows -- plus READ/WRITE/GRANT. The kernel alone mints
them (`bootstrap_device`, `bootstrap_ioport`, `bootstrap_fb`), and
`narrow_device` can only SHRINK a window: an empty window, a sub-window
outside the held one, or an overflowing `lo + len` is `Widening` by name.
Rights and badges travel through narrowing unchanged, because geography
is not authority.

Containers carry the admission policy instead of the geography: a mask of
the capability kinds a holder may receive and a quota of live members.
Root (id 0) admits every kind and has no quota; `create_container` is
bounded at `MAX_CONTAINERS = 8`. The check runs where delivery happens --
a transferred capability whose kind the receiver's container forbids is
`Contained` BEFORE the send touches the queue, and a server in a
container without Reply cannot be called at all. A holder placed in a
container id that does not exist admits nothing. Placement is kernel-only
(`set_container`); no `0x84` row can move a process out of its policy.

The gate row is `0x54 CAP_DEVICE_INFO`: a0 = a handle, a1 = an optional
window out-pointer (`u64::MAX` = none); it answers
`(kind_code << 32) | rights` with the release's kind bits (2 block, 4
I/O ports, 32 framebuffer), and writes `(lo << 32) | hi`. Any other kind
is `EACCES` (an endpoint is not a device), a bad out-pointer is `EFAULT`
before anything is written, and a payload-shaped call is `EINVAL`.

The proof session receives three kernel-minted windows after its
existing slots: the boot device's whole sector space at handle 6, ports
0x80..0x81 at handle 7, and the bound display's rows at handle 8. Ring 3
reads all three back and pins:

```
service-0x84 proof: DEVICE (block 0x20000000e , ioport 0x40000000e
  window 0x8000000081 , fb 0x200000000e rows 0x300 , endpoint
  0xfffffffffffffff3 , shape 0xffffffffffffffea) , DEVICE matches
```

Checks after this slice: `./build.sh` PASS, ledger 21 served calls
(0x40..0x54); `svc84_proof_test.sh` PASS with the DEVICE leg;
`qemu_boot_test.sh` PASS with `experimental caps: 118 clause groups`
(twelve more: window/narrow positive and negative controls, container
admission, quota and bounded-table refusals); `qemu_boot_parity_diff.sh`
PASS (526 chain lines, BIOS == UEFI modulo the allowlist).

Named limit, not hidden: the KINDS and their policy are proven, but the
data-plane rows -- sector read/write, port in/out, pixel store -- are not
served yet. A holder can name and narrow hardware authority; it cannot
yet exercise it through the gate. That is the next slice, and it needs a
real consumer (a driver), not a costume row.

## The release discs carrying the resource kinds (2026-11-09)

Both live discs were rebuilt from this tree with `make_live_iso.sh` after
the resource-kind/container slice and verified outside it with
`check_live_iso.sh --boot --uefi-boot`:

```
dist/nyrqis-live.iso        sha256 6e1e208de61a591ce46023a1d1a612c97e5852ffcef6d6c9bdd6f42b0de890de
dist/nyrqis-live-uefi.iso   sha256 213b24ab0be3e7ceff57a647cc49a6b36fcedf6dc0b8a9389373b62d82078f9c
kernel carried by both:     sha256 b9f036f4a4945705c6e5b4535496b540c683d546a7da6cf14156260d87e93d44
```

Integrity and attribution match, the BIOS disc boots the default entry to a
verified desktop session, and the UEFI disc boots through our signed stub
(carrier walked, GOP handed over, shell rendered and verified).

Named limits unchanged: the UEFI stub is a `dev` build, and the UEFI disc
has no extracted-kernel attribution because the kernel lives inside the
stub binary; its attribution is the one build run that produced both discs
from one `target/kernel.elf`.

## The I/O-port and framebuffer data planes (2026-11-09)

The kinds alone could name hardware authority; the previous slice named
the data plane as the next step. Two of the three now move real bytes
through the gate, each checking authority BEFORE touching hardware:

```
0x55 CAP_IOPORT_IN   a0 = IoPort handle, a1 = port       -> the byte
0x56 CAP_IOPORT_OUT  a0 = IoPort handle, a1 = port, a2 = byte -> 0
0x57 CAP_FB_READ     a0 = FrameBuffer handle, a1 = (x<<32)|y -> raw word
0x58 CAP_FB_WRITE    a0 = FrameBuffer handle, a1, a2 = word -> 0
```

The machine checks kind, the required right, and for ports that the
ported number lies inside `[lo, hi)`. A port one past the window is
`EACCES`, the same answer as a missing right, because the window IS the
authority. The framebuffer rows check kind/right, then the row must lie
inside the capability's row window and the pixel inside the display; a
row outside is `EACCES` before the scan-out is touched. Both refuse a
handle of another kind (`EACCES`, the release's WrongType).

The scan-out helpers (`framebuffer::kernel_read_pixel` /
`kernel_write_pixel`) work on the RAW word, so a word the gate writes is
the same word a read returns; channel order is already applied by the
renderer, and these rows are not a renderer.

The ring-3 proof writes 0xAB to port 0x80 through the I/O-port
capability, reads it back and compares against the kernel's OWN read of
0x80 at verdict time, tries port 0x81 (outside the window), then writes
and reads a raw pixel through the framebuffer capability, tries a row
past the bound display, and tries a framebuffer operation through the
I/O-port handle:

```
service-0x84 proof: DATA-PLANE (port-write 0x0 , port-read 0xff ==
  kernel 0xff , past-window 0xfffffffffffffff3 , fb-write 0x0 ,
  fb-read 0xff00ff , fb-row 0xfffffffffffffff3 , fb-kind
  0xfffffffffffffff3) , DATA-PLANE matches
```

Checks after this slice: `./build.sh` PASS, 25 served calls
(`0x40..0x58`); `svc84_proof_test.sh` PASS with the DATA-PLANE leg;
`qemu_boot_test.sh` PASS with `experimental caps: 121 clause groups`;
`qemu_boot_parity_diff.sh` PASS (526 chain lines, BIOS == UEFI).

Named limit, not hidden: the BLOCK DEVICE's sector data plane is still
not served. The boot device on these discs is ATAPI, so a sector read
needs the SCSI packet path and a real consumer, not a nearly-right ATA
command. The capability names and narrows the sector window; it does not
yet read it.

## The release discs carrying the data planes (2026-11-09)

Both live discs were rebuilt from this tree with `make_live_iso.sh` after
the I/O-port/framebuffer data-plane slice and verified outside it with
`check_live_iso.sh --boot --uefi-boot`:

```
dist/nyrqis-live.iso        sha256 524caef1445dce1b4a5d1240171926db0bcba17e69047f2fe166087083b7c87f
dist/nyrqis-live-uefi.iso   sha256 9cb7cff344f39273d1ceeef670018b4dbfa4c28cb6127b7c8de425736aaa5cf5
kernel carried by both:     sha256 c9e66a83b995faf30bde28bf53722b96fdfc2f4a1eb4cd3dbcc69f6375cef7e8
```

Integrity and attribution match, the BIOS disc boots the default entry to a
verified desktop session, and the UEFI disc boots through our signed stub
(carrier walked, GOP handed over, shell rendered and verified).

Named limits unchanged: the UEFI stub is a `dev` build, and the UEFI disc
has no extracted-kernel attribution because the kernel lives inside the
stub binary; its attribution is the one build run that produced both discs
from one `target/kernel.elf`.

## The block-device sector read (2026-11-09)

The last data plane is served: `0x59 CAP_BLOCK_READ` reads ONE logical
sector from the kernel's boot block device. The machine checks kind, READ
and that the LBA lies inside the capability's sector window BEFORE the
device is touched; a sector at `hi` is `EACCES` by name, like any other
window escape. Then the kernel copies the requested prefix (1..2048
bytes) into a validated user buffer and answers the byte count.

The device path is PIO in `ata::read_sector`, chosen by what actually
completed IDENTIFY:

* ATAPI (these discs' boot device): PACKET + READ(10), 2048-byte
  sectors. The ATA IRQ handler is temporarily barred from draining the
  data phase (it normally drains IDENTIFY's 256 words), and restored on
  every exit path.
* ATA disk: LBA28 READ SECTORS, 512-byte sectors.

Two defects found while porting, both recorded in the code:
the READ(10) transfer length was written at bytes 8..9 instead of 7..8
(the drive answered "zero blocks, no data", status 0x50), and the packet
words were first written big-endian (swapping every CDB byte pair).
`IS_ATAPI` is now set from the command that ACTUALLY completed, not from
the power-on signature, which an earlier probe can consume.

The ring-3 proof reads LBA 16 -- the ISO volume descriptor -- and checks
the descriptor's `CD001` bytes where they landed in its own buffer:

```
service-0x84 proof: BLOCK (read 0x8 , lba16 0x1313030444301
  (want 0x1313030444301) , past-window 0xfffffffffffffff3 ,
  zero-len 0xffffffffffffffea , wrong-kind 0xfffffffffffffff3) ,
  BLOCK matches
```

Checks after this slice: `./build.sh` PASS, 26 served calls
(`0x40..0x59`); `svc84_proof_test.sh` PASS with the BLOCK leg;
`qemu_boot_test.sh` PASS with `experimental caps: 122 clause groups`;
`qemu_boot_parity_diff.sh` PASS (526 chain lines, BIOS == UEFI).

Named limits, not hidden: one sector per call, read-only, no filesystem,
no cache. A block-device WRITE row is not served; the capability can
carry WRITE, but no row exercises it yet.

## The release discs carrying the sector read (2026-11-09)

Both live discs were rebuilt from this tree with `make_live_iso.sh` after
the block-device sector-read slice and verified outside it with
`check_live_iso.sh --boot --uefi-boot`:

```
dist/nyrqis-live.iso        sha256 0464db85eb22e0eeaed14014a23e30ece3329866972cda09e2bf363879a7e068
dist/nyrqis-live-uefi.iso   sha256 a18c36d2d061a173b131ab1b8a9c73143444fea1df79a0f1fe885b0f9f612390
kernel carried by both:     sha256 ab9882da401f75bb777d41750ac756f5ec624928ebea2cb263cbe47b0e78dc1a
```

Integrity and attribution match, the BIOS disc boots the default entry to a
verified desktop session, and the UEFI disc boots through our signed stub
(carrier walked, GOP handed over, shell rendered and verified).

Named limits unchanged: the UEFI stub is a `dev` build, and the UEFI disc
has no extracted-kernel attribution because the kernel lives inside the
stub binary; its attribution is the one build run that produced both discs
from one `target/kernel.elf`.
## The block-device geometry query (2026-11-09)

`0x5a CAP_BLOCK_INFO` answers what the DRIVE says it is, so user space can
size the device before reading it: a 24-byte record of
`{sector size, sector count, 0}`. The same authority rule as the read
applies first -- the handle must be a `BlockDevice` with `READ`, checked
before the device is touched; a handle of another kind is `EACCES` by
name, no device is `ENODEV`, a silent drive is `EIO`.

The geometry comes from the device itself, not from a constant:

* ATAPI: `PACKET` + `READ CAPACITY(10)`, whose big-endian response words
  are rebuilt as BYTES (`{last_lba, block_len}`), not as shifted words --
  a first attempt shifted the words and reported an 8-byte sector.
* ATA disk: `IDENTIFY DEVICE` words 60..61.

The ring-3 proof asks for the geometry and then tries the framebuffer
handle against it:

```
service-0x84 proof: BLOCK-INFO (ack 0x0 , sector-size 0x800 ,
  sectors 0x2e92 , reserved 0x0 , wrong-kind 0xfffffffffffffff3) ,
  BLOCK-INFO matches
```

Checks after this slice: `./build.sh` PASS, 27 served calls
(`0x40..0x5a`); `svc84_proof_test.sh` PASS with the BLOCK-INFO leg;
`qemu_boot_test.sh` PASS with `experimental caps: 122 clause groups`;
`qemu_boot_parity_diff.sh` PASS (526 chain lines, BIOS == UEFI);
`check_live_iso.sh --boot --uefi-boot` PASS.

Named limits, not hidden: geometry is reported, not enforced -- the
sector window is the authority and an LBA past the drive's own capacity
still fails at the drive (`EIO`). Multi-sector reads, writes and a
filesystem remain staged.

## The release discs carrying the geometry query (2026-11-09)

Both live discs were rebuilt from this tree with `make_live_iso.sh` after
the geometry slice and verified outside it with
`check_live_iso.sh --boot --uefi-boot`:

```
dist/nyrqis-live.iso        sha256 00c2653dfe85a3fa219e8db04c7ae4ada0cba9561eb9df8aa61d14e9a272d6ee
dist/nyrqis-live-uefi.iso   sha256 b54a889ab99965e5092a496407798845e97f25975d0f96265dc80fe5d0d344be
kernel carried by both:     sha256 ba9a31e30518f1ccb14c3d11ac4d8c2d3f8e0e17d177ab6cfb1c93b9e0e86366
```

Integrity and attribution match, the BIOS disc boots the default entry to a
verified desktop session, and the UEFI disc boots through our signed stub
(carrier walked, GOP handed over, shell rendered and verified).

Named limits unchanged: the UEFI stub is a `dev` build, and the UEFI disc
has no extracted-kernel attribution because the kernel lives inside the
stub binary; its attribution is the one build run that produced both discs
from one `target/kernel.elf`.
## Block spans, writes, and the ISO9660 probe (2026-11-09)

`0x59 CAP_BLOCK_READ` now takes a SPAN: up to 16 KiB (eight 2048-byte
sectors) in one call. The authority check is over the whole span -- the
first sector being inside the window is not enough, and a span whose last
sector escapes is `EACCES` before the device is touched. The kernel reads
the span sector-by-sector and copies the requested prefix.

`0x5b CAP_BLOCK_WRITE` is the write half: kind, WRITE and window
containment first, then one sector through `ata::write_sector` (LBA28
WRITE SECTORS). The packet path in this kernel is read-only, so ATAPI
media answers `EROFS` (a new named refusal, -30) AFTER the right check --
authority passing is observable separately from the media refusing.

The ring-3 proof uses both: it reads LBA 16 plus LBA 17 in one 4 KiB span
(the ISO9660 primary volume descriptor and the boot record that follows
it), copies the descriptor's 32-byte volume id out of its own buffer, and
the kernel compares that with its own read of the same sector. It also
feeds a write through the `WRITE`-carrying block handle (ATAPI ->
`EROFS`), a write past the window (`EACCES`) and a write through the
framebuffer handle (`EACCES`).

```
service-0x84 proof: BLOCK-SPAN (ack 0x1000 , past-window
  0xfffffffffffffff3 , zero-len 0xffffffffffffffea , media
  0xffffffffffffffe2 , write-window 0xfffffffffffffff3 , write-kind
  0xfffffffffffffff3) , BLOCK-SPAN matches
service-0x84 proof: BLOCK-VOLUME (id "ISOIMAGE" , pvd
  0x0001313030444301 , next 0x4501313030444300 , ring3-id matches) ,
  BLOCK-VOLUME matches
```

One bug found on the way: the second descriptor on these discs is the El
Torito boot record (type 0, `EL TORITO` at byte 7), not a terminator, so
the chain check is "every descriptor carries CD001 and version 1", not
"the next sector is 0xFF".

Checks after this slice: `./build.sh` PASS, 28 served calls
(`0x40..0x5b`); `svc84_proof_test.sh` PASS with the BLOCK-SPAN and
BLOCK-VOLUME legs; `qemu_boot_test.sh` PASS with `experimental caps: 122
clause groups`; `qemu_boot_parity_diff.sh` PASS (526 chain lines,
BIOS == UEFI); `check_live_iso.sh --boot --uefi-boot` PASS.

Named limits, not hidden: a span is at most eight sectors; there is no
cache and no filesystem layer in the kernel -- the ISO9660 parsing in the
proof is ring-3 code. The ATA write path exists but no test media is
writable, so the LBA28 write itself is not exercised by a gate; only its
authority and media refusals are.

## The release discs carrying spans, writes and the volume probe (2026-11-09)

Both live discs were rebuilt from this tree with `make_live_iso.sh` after
this slice and verified outside it with `check_live_iso.sh --boot
--uefi-boot`:

```
dist/nyrqis-live.iso        sha256 409fc896bd134ab5408ff3485c00bb70925d885fe7394e82d97ad9ccf2feac19
dist/nyrqis-live-uefi.iso   sha256 28aff806e46c6ea8415bf3625740f4c2de634611ebe546c8557052e3d52d3e0d
kernel carried by both:     sha256 7624ac0efbde0eec236a9d27abbedb76f067543ee408dc5df162247fda1b405f
```

Integrity and attribution match, the BIOS disc boots the default entry to a
verified desktop session, and the UEFI disc boots through our signed stub
(carrier walked, GOP handed over, shell rendered and verified).

Named limits unchanged: the UEFI stub is a `dev` build, and the UEFI disc
has no extracted-kernel attribution because the kernel lives inside the
stub binary; its attribution is the one build run that produced both discs
from one `target/kernel.elf`.
## Sector cache, writable ATA media, and a ring-3 root walk (2026-11-09)

Three follow-ons landed together on the block data plane.

**The sector cache.** `ata::read_sector_cached` is a direct-mapped cache of
eight whole sectors; a repeat read of the same LBA is a memory copy, a
miss installs the sector, and a write INVALIDATES the sector it touches.
`0x5c CAP_BLOCK_CACHE_STATS` exposes `{hits, misses}` so the proof can
observe the cache rather than trust it: it queries, reads LBA 18 twice,
queries again, and the kernel checks the hit counter rose.

**Block capabilities name their device.** The `Capability` struct gained a
`unit` (the IDE channel) for BlockDevice, set by `bootstrap_device` and
carried unchanged through a transfer. 0x59/0x5a/0x5b/0x5c use the handle's
channel instead of hard-coding the boot channel, which is what lets the
proof hold the ATAPI boot disc and a writable ATA disk at once.

**A real LBA28 write.** The proof harness now attaches a 4 MiB raw disk on
the secondary IDE channel (the boot ISO is primary master, `-boot d`), and
the equip mints it as block handle 9. The program writes a pattern to LBA
1000, reads it back through the same capability, and the kernel reads that
LBA itself: all three copies must agree. `probe_ata_disk` finds the disk
with a polled signature read + IDENTIFY, so the boot's IRQ path is not
required.

**The ISO9660 root, walked in ring 3.** The program takes the primary
descriptor's root directory record (extent at PVD+158, length at +166),
reads the directory through the block capability, and walks the records
itself, emitting `[len][name]` pairs and skipping self/parent. The kernel
re-walks the directory from its own read and compares the sequences.

```
service-0x84 proof: BLOCK-CACHE (hits 1 -> 2 , misses 2 -> 3 , queries
  0x0/0x0) , BLOCK-CACHE matches
service-0x84 proof: BLOCK-SCRATCH (write 0x200 , read 0x200 , disk and
  both ring-3 copies agree yes) , BLOCK-SCRATCH matches
service-0x84 proof: BLOCK-ROOT (entries 8 , names "BOOT,BOOT.CAT;1,
  BUILD.LOG;1,EFI,EFI.IMG;1,MACH_KER.;1,SYSTEM,_DISK") , BLOCK-ROOT matches
```

Two consequences worth naming: the scratch capability takes slot 9, so the
later proof handles shift by one (REPLY's handle is 0xa, MAP's second
region 0xa) -- the harness now asserts those; and the proof's QEMU command
names the drives explicitly instead of using `-cdrom`, because `-cdrom`
already occupies the secondary master where the scratch disk belongs.

Checks after this slice: `./build.sh` PASS, 29 served calls
(`0x40..0x5c`); `svc84_proof_test.sh` PASS with the BLOCK-CACHE,
BLOCK-SCRATCH and BLOCK-ROOT legs; `qemu_boot_test.sh` PASS with
`experimental caps: 122 clause groups`; `qemu_boot_parity_diff.sh` PASS
(526 chain lines, BIOS == UEFI); `check_live_iso.sh --boot --uefi-boot`
PASS.

Named limits: the cache is eight sectors and direct-mapped (no write-back,
no readahead); the scratch disk is a proof fixture, not a mounted volume;
and ISO9660 lives in ring 3 -- the kernel still has no filesystem layer.

## The release discs carrying the cache, disk write and root walk (2026-11-09)

Both live discs were rebuilt from this tree with `make_live_iso.sh` after
this slice and verified outside it with `check_live_iso.sh --boot
--uefi-boot`:

```
dist/nyrqis-live.iso        sha256 df59c4c439d5dfc614c2e6ae00d14c599204b67ed653dd00fc04b46b68d1b778
dist/nyrqis-live-uefi.iso   sha256 09463753731f4334e4188d3820724504acb14bf58fb6830582906fb5e0ad326e
kernel carried by both:     sha256 45db0360a7da22b9e2ff150b6faa0e4db21c7062fd53a68faa0d9d78ccf911da
```

Integrity and attribution match, the BIOS disc boots the default entry to a
verified desktop session, and the UEFI disc boots through our signed stub
(carrier walked, GOP handed over, shell rendered and verified).

Named limits unchanged: the UEFI stub is a `dev` build, and the UEFI disc
has no extracted-kernel attribution because the kernel lives inside the
stub binary; its attribution is the one build run that produced both discs
from one `target/kernel.elf`.

**2026-11-15 — platform identities (Android / Windows / macOS / console / native), firmware-driven auto-detection, and a runtime identity switch:**

- **`crate::platform_mode` is a real module, not a skin table.** Five identities (`nyrqis`, `android`, `windows`, `macos`, `console`), each with its own palette (colour roles in one place, never inside a renderer) and its own chrome contract: the native mycelium mark + wordmark + accent rule, Android's accent chip and gesture pill, Windows' bottom taskbar with the four-square start mark and a window title bar with minimise/maximise/close, macOS' top menu bar + dock + traffic lights, and the console identity's terminal mark. Every identity's apply reads back the pixel only that skin paints; a mismatch fails the boot.
- **The machine's firmware chooses the identity, and says which signal it used.** `acpi.rs` now reads the FADT's OEM ID and OEM TABLE ID out of the checksum-verified table walk (it previously only counted tables), and `devices::pci::Inventory` now keeps up to eight distinct vendor IDs (`has_vendor`). Detection order: Apple/Microsoft ACPI OEM ID, then Apple/Microsoft PCI vendor, then a phone-sized panel (< 800 px) as the Android posture, otherwise the native identity. `nyrqis.persona=` overrides all of it, and an unknown token is REPORTED and the detected identity used — the same rule `nyrqis.mode=` follows, so a typo in a boot entry cannot look like it worked.
- **Switching is a runtime operation through one code path.** `platform_mode::switch` repaints `device_mode::render` first (so no pixel of the previous posture can survive), applies the requested identity, reads its landmark back and publishes a surface hash. Keys `1..5` drive that same function during the boot session's one-second hotkey window; releases are ignored, every switch prints its hash and its read-back verdict. `platform_mode::current()` publishes the live identity.
- **The cycle proof.** Under `qemu_test` (or `nyrqis.persona_selftest=1`) every boot renders all five identities through that same switch, asserts each read-back, asserts the five surface hashes are genuinely distinct, and restores the selected identity — so "the identities are different pictures" and "switching works" are boot facts, not claims. The native identity deliberately draws AROUND the device renderer's landmarks (accent block, dock, FAB, shell pane) and its probe pins the shared status strip and accent block, so the existing `device_mode_test.sh` pixel contract is preserved rather than weakened.
- **One placeholder removed, for real:** console device mode's "three completed prompt lines, as solid rule blocks (no font yet)" are gone. `device_mode::draw_text` renders through the release's own 5x7 font table (`shell::text`) and applies the loader's scan-out order to exactly the rows written, in one place; the console now draws a real prompt (`NYRQIS>`), the mode line and the session facts.
- **The live ISO gains the identity dimension:** `tools/make_live_iso.sh` writes a GRUB submenu with one entry per identity (`nyrqis.persona=...`) beside the existing per-device-mode entries, so the disc and the kernel's parser cannot drift (both read the same five tokens).

**2026-11-15 — the platform identity on the `0x84` gate (0x5d / 0x5e), proven from ring 3:**

- **`PLATFORM_GET` (0x5d) and `PLATFORM_SET` (0x5e)** join the surface, numbered in its own `0x40..` range (checked disjoint from the release ledger by `tools/check_svc84_ledger.py`, now 31 served calls). GET answers `Platform::ALL`'s index of the kernel's LIVE identity; SET validates the index, repaints the device chrome and applies that identity through the same `platform_mode::switch` the hotkeys use, then reads the skin's landmark back — returning `0`, `-EINVAL` for a number no identity owns, `-ENODEV` when this machine has no bound display, or `-EIO` when the skin's own read-back failed. The successful switch never reports success it did not verify.
- **The gate can render without owning the boot session's display.** `framebuffer::from_bound()` constructs a second handle onto the one published mapping (base, stride, geometry and the loader's scan-out order are now all published at `init`), so a trap-entered service call can repaint the surface the session is looking at. `device_mode::select` publishes the chosen form factor (`device_mode::current()`), so the switch redraws the same layout the boot chose instead of guessing.
- **Ring-3 proof, in `prog_svc84.s` and the task-0 verdict.** A real user process reads the identity (BUF+11296), switches to the Windows posture (BUF+11304), reads it back (BUF+11312), has `SET 99` refused `-EINVAL` (BUF+11320), confirms the refusal left the identity untouched (BUF+11328), restores the ORIGINAL identity — read out of the buffer, never assumed to be 0, so a machine that auto-detected another posture still restores correctly (BUF+11336) — and reads it back again (BUF+11344). The coordinator compares all seven words against the kernel's own live identity at verdict time, with the no-display case checked as its own `-ENODEV` contract rather than skipped, folds the result into the proof's `ok`, and prints a `PLATFORM (...) , PLATFORM matches` line that `tools/svc84_proof_test.sh` now asserts.
- **Ring-0 self-test** covers the rows' kernel side without repainting a normal boot: GET must equal the live identity, and out-of-range SETs must be refused by name. `serve_platform_set` is `#[inline(never)]` for the same measured reason `serve_region_request` is — `dispatch`'s frame is shared by every arm, and the repaint path is deep.

**2026-11-15 — `PLATFORM_INFO` (0x5f), the session shape in one record:**

- **One call, six words, all compared live.** `PLATFORM_INFO` (a0 = 48-byte record) validates the whole record through the user-range walker *before* writing, refuses `-ENODEV` when no display is bound, and then writes `{identity, form factor, display width, display height, status-bar height, accent RGB}` as six u64s. Nothing is copied from a boot-time snapshot: the identity and form factor come from the same `current()` values the switch and the gate use, the geometry from `framebuffer::bound_mode()`, and the status-bar height from `device_mode::layout(mode, w, h)` — so a session that switches identity or runs on a different panel cannot receive stale numbers. `DeviceMode` gained a public numeric `id()` for the form-factor word.
- **Ring-3 proof.** `prog_svc84.s` now asks for the record at BUF+11400 (ack at BUF+11360) after the GET/SET/restore sequence; the task-0 verdict compares all six words with the kernel's own live identity, form factor, bound geometry, layout status height and skin accent, with the no-display case checked as `-ENODEV`. The result folds into the proof's `ok` and prints as `PLATFORM-INFO (...) , PLATFORM-INFO matches`, which `tools/svc84_proof_test.sh` asserts (pinning only the machine-independent words).
- **Ring-0 self-test** covers the validator half only — a kernel-stack record pointer and a null one are both refused `EFAULT` before any write — because the served half belongs to the ring-3 proof. The ledger gate now reads **32 served calls (`0x40..0x5f`)**, still disjoint from the release's `0..28`, with every served number exercised by the proof programs.

**2026-11-15 — `PLATFORM_MODE_SET` (0x60), the form factor as a runtime request:**

- **`PLATFORM_MODE_SET`** (a0 = form-factor index) re-renders the DEVICE chrome at the requested layout through `device_mode::render`, then re-applies the current identity on top of it, and publishes the new mode with the new `device_mode::set_current` **only after both read-backs verify**. A render that fails its probes changes nothing and answers `-EIO`; an index no form factor owns is `-EINVAL` before anything is drawn; no bound display is `-ENODEV`. `DeviceMode` gained `from_id`.
- **One source of truth for "which layout is on screen"**: the custom form factor, the identity switch, `PLATFORM_INFO` and a second request all read the same published value, so a record can never report a layout the display is not in.
- **Ring-3 proof:** `prog_svc84.s` asks for the phone layout, reads the resulting `PLATFORM_INFO` record (BUF+11456), then asks for the ORIGINAL form factor — read out of the first record at BUF+11408, never assumed — and reads that back (BUF+11512). The task-0 verdict compares both records' identity, mode word, bound geometry and status-bar height against `device_mode::layout` at the mode the kernel then reports, confirms `device_mode::current()` went back to the original, checks the headless `-ENODEV` contract, folds the result into the proof's `ok`, and prints `PLATFORM-MODE (...) , PLATFORM-MODE matches`, asserted by `tools/svc84_proof_test.sh` on its machine-independent words.
- **Ring-0 self-test** covers the refusal half (index 4 and `u64::MAX` are `-EINVAL`, before any repaint); a served request belongs to the ring-3 proof because it paints the display. The ledger gate now reads **33 served calls (`0x40..0x60`)**, still disjoint from the release's `0..28`, every served number exercised by the proof programs.

**2026-11-15 — display ownership (0x61 / 0x62 / 0x63), the valve the identity rows were missing:**

- **A fourth kernel module, `src/display.rs`.** Until this increment the mutating rows were a pipe with no valve: any ring-3 caller that reached the gate could repaint the display under every other process. `DISPLAY_ACQUIRE` (0x61) claims the display for the CALLER's gate session — the id `scheduler::session_identity()` recorded at the ring transition, never a number the caller supplies — `DISPLAY_OWNER` (0x63) answers the holder or `0`, and `DISPLAY_RELEASE` (0x62) gives it back. One owner at a time: a second session's claim is `-EBUSY`, a bystander's release is `-EACCES`, and ring 0's claim is `-EPERM` because the kernel is not a tenant of the display it is running on.
- **The rows now consult the rule.** `PLATFORM_SET` (0x5e) and `PLATFORM_MODE_SET` (0x60) refuse any ring-3 session that is not the owner with `-EACCES`; unowned, anyone may mutate, which is why the earlier boot and ring-3 setup legs keep working unchanged. The authority rule is one pure function, `display::may_mutate(owner_session, caller)`, so the served path and the kernel's own test cannot drift apart.
- **Kernel state, not pixels.** The identity and form factor live in the `platform_mode`/`device_mode` statics, so what an owner sets outlives the owner's process; the ownership itself does not — the session-withdrawal site in `CALL_CAP_EXIT` calls `display::release_if_owner`, so a dead or exiting owner can never freeze the display for everyone. Boot asserts `display::owner() == NO_OWNER` before any session exists.
- **Ring-3 proof:** `prog_svc84.s` asks `DISPLAY_OWNER` (0 — unowned, which is why its own rows above were allowed), acquires (0x61), reads back its own session id from 0x63, then sets the Windows identity and the phone form factor INSIDE that ownership, reads both back through `PLATFORM_INFO` (BUF+11664), releases (0x62, owner answers 0 again), re-acquires, and EXITs without releasing so the withdrawal path does the clearing. At verdict time, after both processes are gone, task 0 checks that the kernel still reports the identity and form factor that owner chose (`live identity`, `live mode`) and that **no owner remains** — the two halves of "persists past the session, without leaking".
- **Ring-0 self-test** exercises the real functions rather than fakes: acquisition from ring 0 is `-EPERM`, a session that holds the display makes `may_mutate` false for another session and true for itself, a second claim is `-EBUSY`, the owner's release succeeds and the display is free again. The ledger gate now reads **36 served calls (`0x40..0x63`)**, still disjoint from the release's `0..28`, with every served number exercised by the proof programs.

**2026-11-15 — the supervised display server (0x64..0x68), per-client authority:**

- **A display has a server, not a single owner.** `DISPLAY_SERVE` (0x64) makes the calling session the display SERVER and publishes a POLICY mask (`AUTH_IDENTITY` = 1 for `PLATFORM_SET`, `AUTH_MODE` = 2 for `PLATFORM_MODE_SET`, 3 for both). `DISPLAY_ATTACH` (0x65) attaches another session as a CLIENT granted `requested & policy`, returned packed as `(slot << 32) | grant`; `DISPLAY_DETACH` (0x66) frees the slot; `DISPLAY_CLIENT` (0x67) queries any session's grant (a query, never authority); `DISPLAY_STATUS` (0x68) answers `server << 32 | client count << 16 | caller grant`, the server reporting `3` for itself. Four client slots, claimed with a compare-exchange so two attaches cannot win the same one.
- **Authority is now per call, not per session.** `PLATFORM_SET` consults `AUTH_IDENTITY` and `PLATFORM_MODE_SET` consults `AUTH_MODE` through `display::may_mutate_bit`, so a client can be allowed to switch the identity and refused the form factor in the same breath — and the refusal happens by AUTHORITY before the device is consulted, which is why it is `-EACCES` even on a machine with no display bound. Without a server the older exclusive form still rules (0x61/0x62/0x63), and the two forms refuse each other with `-EBUSY`; the server form is checked inside the claim window, so an owner appearing mid-claim is caught rather than raced.
- **Supervision outlives its clients; the policy does not outlive its server.** `display::release_on_withdrawal` runs at the one place a gate session dies. A client's death frees its slot and leaves the server, its policy and its authority standing. A server's death stops the server, clears the policy and detaches every client — an orphaned client table would refuse every later session for the rest of the boot. The display's identity and form factor live in kernel statics, so they survive either: the authority dies, the picture stays.
- **Ring-3 proof, two processes.** `prog_svc84.s` SERVES the display as its very first call (policy = identity only) and takes `DISPLAY_STATUS` before it ever parks: server = its session, zero clients, grant 3 — deterministic because no client can have attached yet. `prog_svc84_peer.s` then attaches as a client (retrying `-ENODEV`, bounded, with the retry count at PEN+336), asking for BOTH bits and receiving exactly 1; it switches the identity through that grant (`0`) and is refused the form factor by name (`-EACCES`), queries its own grant, detaches, and re-attaches into the same slot. After the rendezvous the server checks `DISPLAY_STATUS` again (one client, its own grant 3), attempts `DISPLAY_ACQUIRE` (`-EBUSY`: a served display is not a claimable one), `DISPLAY_RELEASE` (`-EACCES`: a client cannot unseat a server), switches the identity AND the form factor — the form factor being exactly what no client could do — reads both back through `PLATFORM_INFO`, and EXITs. The verdict then confirms that the server's withdrawal dissolved the server and detached the still-parked client, while the identity and form factor it set are still the kernel's live state.
- **Ring-0 self-test** exercises the same functions: ring 0 can neither serve, attach, detach nor claim (`-EPERM`); attach before any server is `-ENODEV`; a second server is `-EBUSY`; an empty or unknown policy is `-EINVAL`; an exclusive claim beside a server is `-EBUSY`; a client asking for both bits gets 1; `may_mutate_bit` says yes for its granted bit, no for the other, yes for the server, no for a stranger; attaching twice is `-EBUSY`; a mask granting nothing is `-EACCES`; detaching twice is `-EACCES`; the slot is reusable; **a client's withdrawal leaves the server standing and a server's withdrawal dissolves everything it supervised**. The ledger gate now reads **41 served calls (`0x40..0x68`)**, still disjoint from the release's `0..28`, with every served number exercised by the proof programs.

**2026-11-15 — client surfaces as mapped regions and server-side composition (0x69..0x6a):**

- **A surface is a capability-mapped region, not a copy of one.** `DISPLAY_SURFACE` (0x69, `a1` = 32-byte record) checks that the caller is an attached client without a surface (refused BEFORE anything is requested, so a refusal never leaves a region behind), asks the release's own region path for ONE page (`caps::request_region_for`), records the region id and the minted handle in the client's slot, and writes `{base, len, rights, handle}` to the caller. The client then draws with ordinary stores into `base` — there is no per-pixel call in the surface path any more (the short-lived 0x6a `DISPLAY_SURFACE_PIXEL` row is gone, superseded by real drawing).
- **The cross-address-space window already had a name: the mirror.** `svc84_proof::allocate_region_mapping` maps a requested region through the requester's mapper and then calls `mirror_into_other_space`, which maps the SAME frames at the SAME virtual address in the OTHER proof address space (all-or-nothing; a failed mirror unrolls the requester's mapping). That is what lets the capability handover work, and it is exactly what a compositing server needs: `DISPLAY_COMPOSE` (0x6a) reads each client surface through the window already present in the SERVER's address space, so composition involves no CR3 switch and no pixel copy. The row also checks the region is at least one tile (`-EIO` otherwise), blits in slot order, and reads its own first and last pixel back out of the framebuffer.
- **The refusal set is unchanged in spirit:** `-EPERM` from ring 0, `-EACCES` for a non-client, `-EBUSY` for a second surface, `-EFAULT` for a bad record pointer, `-ENODEV` for compose with no display, `-EACCES` for compose by anyone but the server, `-EIO` for a blit that did not land.
- **Ring-3 proof:** `prog_svc84_peer.s` asks for its surface (record at PEN+352), reads the base the KERNEL chose out of that record, and draws `0x224466`, `0x335577` and `0xabcdef` (at (3,2)) with three plain `movl`s. The server composes after it sets the form factor; the verdict computes the surface origin with the kernel's own placement rule and reads those three words back out of the FRAMEBUFFER, and the peer's record is checked for a non-zero base, a 4096-byte length and the READ|WRITE|GRANT rights the capability actually carries.
- **Ring-0 self-test** covers the bookkeeping half (the region itself needs the gate's mapping source, which the ordinary boot does not install): ring 0 may neither ask nor compose (`-EPERM`); a non-client has no surface (`-EACCES`); the record lands in the caller's own slot and reads back as the same id/handle pair; a second ask is `-EBUSY`; and detaching the client empties the surface table. The ledger gate now reads **43 served calls (`0x40..0x6a`)**, still disjoint from the release's `0..28`.

**2026-11-15 — the session-to-address-space registry and general surface windows (0x6b):**

- **`src/experimental/space_registry.rs`** answers the question the display work kept asking with proof-only statics: *which address space does this session live in?* Eight rows of `session -> PML4`, atomic, with `register` (idempotent for the same pair, `-EBUSY` for a session already recorded against a DIFFERENT space, `-EINVAL` for zeros), `register_active` (the space the CPU is running on — the caller's own fact at the moment it starts serving), `pml4_of`, `session_of`, `unregister` and `count`. Lookups answer `None`, never a guess: a session that was never registered has no space as far as the module is concerned. Several sessions may share one space; the table is per session and does not deduplicate.
- **Lifecycle.** The 0x84 proof registers both of its sessions when it builds their spaces (asserted with a panic, so a drift fails at the source instead of becoming a window nobody can map). A display server registers itself through `DISPLAY_SERVE` with its active CR3; if the registry refuses, the serve is UNDONE rather than left standing as a server the kernel could not locate. A session's withdrawal unregisters it alongside the display and capability teardown.
- **`src/experimental/region_window.rs`** installs the general half of the region hooks — map, release, unmap — over the boot's own frame allocator, at a window range of its own (`0x4000_0000_0000`, one 64 KiB slot per request, eight slots). It mirrors each window into the display server's registered space at the same virtual address over the same frames, all-or-nothing: a partial map or a refused mirror is unrolled before the refusal. When the server already shares the caller's space the mirror is skipped and the same address is the window. The module is installed once during boot, BEFORE any session can serve a display; the proof boot installs its own hooks afterwards and overwrites these, so none of the proof's frame accounting changes.
- **The row.** `DISPLAY_SPACES` (0x6b) answers `count << 32 | server-registered << 1 | caller-registered` — the facts a session needs to know whether a window can be placed for it and for its server — and hands no PML4 address to ring 3.
- **Proof.** In the proof boot the registry holds the two processes' spaces and the caller is the display server, so the ring-3 row must answer `0x2_0000_0003`; after the server's withdrawal the verdict confirms the caller's row is GONE (`pml4_of(session).is_none()`) while the identity, form factor and composed surface pixels still stand. The boot self-test checks the table's own rules (idempotent register, conflicting re-register, zero session/space refused, lookups, duplicate-space rows for two sessions, unregister twice) and that the table is empty again afterwards, and that ring 0's row answers zero.
- The ledger gate now reads **44 served calls (`0x40..0x6b`)**, still disjoint from the release's `0..28`.

**Known limit, unchanged by this increment:** the registry says where a session's space is; it does not give a session a private space. On a plain boot the demo sessions share the boot's space, so surfaces and composition work without any mirror, and a session that later gets its own space must be registered for the mirror to be placed.

**2026-11-15 (follow-on) — surface window slots are reclaimed, not consumed:**

- `region_window` used to advance a slot counter and never look back: eight requested regions in one boot, and every later `DISPLAY_SURFACE` was refused `-ENOSYS` for the rest of that boot. Slots are now a reclaimed table — one bit per slot in an atomic bitmap, taken with compare-and-swap (so two sessions asking at once cannot be given the same window) and cleared by the region's releaser.
- **A failed request costs nothing.** `window_mapping` only mints a base after a slot is held, and returns the slot on every failure path (allocation failure, partial map, refused mirror), so a refused surface no longer burns a window.
- **Mirrors are released where they were made.** Each slot records the PML4 its mirror was placed in; the releaser unmaps that space, not whatever space the display server has since moved to or withdrawn from. Previously a release could leave a stale window in a space the server had already left while freeing the frames under it.
- **The stride now covers the largest request.** `WINDOW_STRIDE` is derived from `MAX_PAGES` (64 pages = 256 KiB) instead of a fixed 64 KiB, so two live windows cannot overlap however many pages each claimed — the previous stride aliased any request above sixteen pages onto its neighbour.
- **`unmap_window` (one holder dropping ITS window) still does not free the slot**, because it does not free the frames: the slot returns with the region, not with one holder's mapping.
- **Boot coverage.** `slot_self_test()` runs during boot, before anything can ask for a window: every slot can be taken once, a full table REFUSES rather than overlapping, releasing the odd slots gives exactly those bases back in ascending order (reuse is lowest-free, not unbounded growth), bases off a slot boundary or past the last slot are nobody's window, and the table is empty afterwards. The boot transcript prints the slot table's verdict alongside issued/live counts.

**2026-11-15 (follow-on) — the surface churn proof leg (0x6c):**

- **Surfaces stopped borrowing the capability machine's windows.** `DISPLAY_SURFACE` used to request a region through the same mapper as every other region: on the proof boot that is the proof's MONOTONIC window table (its bases are pinned by the verdict), so every surface a client ever opened spent one of eight slots for the rest of the boot. Surfaces now map from their own reclaiming table in `region_window` (`map_surface`, `release_surface`), armed on the ordinary boot and on the proof boot alike, backed by the same frame allocator so the frame accounting still covers them.
- **The capability is minted over a mapping the kernel made** (`caps::adopt_region_for`, `owned: false`), so a close or an exit never frees a surface's frames: the display that made the window gives it back. That split is what keeps the surface's lifetime the display's business.
- **`DISPLAY_SURFACE_RELEASE` (0x6c)** is the release direction: the record goes first (`-ENOENT` on a second release by name), then the capability, then the window -- frames, mirror and slot -- and the answer packs `live << 32 | issued`. A withdrawal does the same for a session that dies holding a tile, so a dead client's window cannot stay mapped (or its slot spent) for the rest of the boot.
- **The ring-3 leg.** The server attaches itself as a client (0x65), then opens and gives back a surface tile TWELVE times (0x69/0x6c against an eight-slot table), and detaches. Twelve is the point: a table that consumed slots would refuse the ninth.
- **What the verdict pins** (from the kernel's own counters, read out of the program's results page): twelve completed open/release pairs, zero failures, a positive self-attach grant, the LIVE window count identical before and after the churn, and eleven further windows ISSUED between the first and last release answers (the first opening is already inside the "before" number because that answer is the first release's). It also requires the surface table to be EMPTY at verdict time, so every window came back. The base comparison is recorded and printed but deliberately not asserted: the peer's own surface can be given back inside the churn, which changes which free slot the churn takes without saying anything about reclamation.
- **One release path.** The row (0x6c) and a session's withdrawal call the same `release_surface_window`: record, capability, window. Two paths would be how a tile ends up unmapped with its slot still spent, or freed twice. The withdrawal calls it BEFORE the display's own withdrawal, which drops the client slot and the surface record with it -- a tile released after that would have no window left to free.
- **The display's surface record carries the window.** `surface_record` stores `{region, handle, base, len}` and `surface_take` answers all four, so the release frees the window it must free without asking the capability machine where it is. Taking the record first and looking the geometry up afterwards was a dropped tile with a live window whenever that lookup failed. Composition reads the same record (`surface_geometry`) instead of the capability table, so the display knows where its own windows are.
- **The verdict's empty-table check is tied to a counter.** `surface_in_use() == 0` alone could pass on a table that never armed; it now also requires `surface_issued() == churn[4]` -- the issue count the program saw at its last release, which nothing can open a surface after -- and `churn[4] >= 12`, so "empty" means a churn's worth of windows was issued and came back.
- **The tile teardown contract.** A client's tile is part of its client slot, and no path may drop the record while the window is live. Two paths used to: `detach` (0x66) cleared the record, and `stop` (a server's death) cleared every supervised client's record. Both leaked the window.
  - **`detach` refuses `-EBUSY` while the caller holds a tile.** The client gives it back with 0x6c first, which frees the window in the space that maps it. Dropping the record at detach would leave a window nothing names; freeing it there would free pages this context cannot reach, and would leave the client's own capability naming a window whose slot is about to be handed to another client.
  - **`stop` PARKS the record under its owner's session** (`SURFACE_OWNER`) instead of dropping it: a server's context cannot free a window that lives in a client's address space. `surface_take` resolves either a client's own slot or a record parked under its session, so the owner gives the tile back with 0x6c -- or its own death does.
  - **One release path.** `release_tile` closes the capability (best effort) and gives the window back; the 0x6c row, `release_on_withdrawal`, and the fault path all end there. `release_on_withdrawal` now RETURNS the dying session's own tile instead of freeing it in place, and `abandon_gate_session` -- the fault-kill path, which previously released no display state at all -- uses it too, so a task that dies holding a tile still gives its window back in its own space.
- The boot self-test pins the refusal: a client with a recorded tile cannot detach (`-EBUSY`), and the tile's geometry survives the round trip through `surface_geometry` and `surface_take`.
- **The parking path has its own coverage** (it had none): an attached client records a tile at its own base, the server is stopped, and the test checks the tile was PARKED rather than dropped -- server and client slots gone, the record and its window still accounted for -- then takes it BY SESSION through `surface_take` (no client slot left to resolve it through), checks a second take is refused `-ENOENT` (consumed, not hidden), and checks the client can no longer detach (`-EACCES`: its slot went with the server). The parked base is deliberately different from the earlier tile's, so the geometry that comes back can only be the parked record's own.
- The ledger gate now reads **45 served calls (`0x40..0x6c`)**, still disjoint from the release's `0..28`.

**2026-11-20 — one shell, not a choice: Android's home screen and Windows' taskbar, styled by the HIG with none of Apple's assets:**

- **The shell was half a product.** `porting/release-1-71/shell/src/ui.rs` rendered a Material 3 home screen — a status bar, a clock widget, quick settings, an app grid and a search pill — and nothing of the other desktop model the same machines run: no taskbar, no running-app surface, no window controls. A user was being made to pick a platform before they could work, which is the one thing a shell must not do.
- **The functions are now the union.** The bottom 40/44 dp carries a taskbar (Start, one search field, running-app buttons on Expanded, a clock+battery tray); the home screen above it keeps Android's widgets, quick settings and app grid; and on Medium/Expanded the app grid is framed as a Windows window with a title bar and minimise / maximise / close. All three window controls are drawn here from rectangles — a line, an outlined square, an X — so nothing is borrowed and, deliberately, Apple's traffic-light circles are NOT reproduced: they encode three different functions in colour alone, which the HIG's own accessibility rule forbids, while line/square/X reads at any colour vision.
- **One search field, not two.** Android's bottom search pill and Windows' taskbar search answer the same question, so the union keeps one — in the taskbar — instead of making the user learn which of two identical boxes to type in. For the same reason the gesture pill is gone: navigation and the taskbar both claim the bottom inset, and the taskbar carries functions where the pill carried only navigation.
- **The look is the HIG's, applied as numbers.** Everything sits on the 8 dp grid (24/16 dp margins, 8 dp gaps), the taskbar and its controls are ≥ 44 dp targets once their padding is counted, one accent (primary) is the only saturated fill, and radii follow the role scale (8 controls, 12 window title, 16 window, 28 pills and widgets).
- **Verified from outside the guest.** `tools/device_mode_test.sh` PASSes on a real boot: desktop/tablet/phone/console each render, the kernel's own read-back verifies the pane, all four screendumps differ, and the selection source is stated. The host renderer (`host/hostrender.rs`) renders the same source at 360x640, 1024x700 and 1440x900, which is how the layout was reviewed before the boot.
- **Limit, unchanged:** the shell is still a renderer, not a session — no input routing, no owning user-space process. The committed live discs still carry the previous kernel, so the new shell reaches a bootable disc at the next `make_live_iso.sh` run.

**2026-11-20 (follow-on) — one bottom bar, and a hairline shell:**

- **The duplication the union exposed is gone.** The shell drew a taskbar inside the content pane while each device mode drew its own bottom chrome under it (desktop a 72 px dock, tablet a 24 px gesture inset with a pill, phone a 56 px bottom navigation strip): two bars on one edge, each a launcher or a navigation model for the same machine. The mode contracts no longer reserve a bottom strip at all — `content_pane` runs to the display's last row and the shell's taskbar is the one bottom bar, at every form factor. The shell exports `bar_height(w)` so the phone FAB (and the tests) place themselves above it instead of guessing.
- **The gate moved with the contract, and proves the new one.** `tools/device_mode_test.sh` used to read three now-absent landmarks (desktop's dock destination, tablet's gesture pill, phone's bottom navigation). It now reads the shell bar's Start mark — the one accent fill on the bar — 22 dp above the display's last row in EVERY framebuffer mode, which is also the proof that no mode still draws chrome under the shell: a dock, a pill or a nav strip would push the bar up and the probe off the accent. The probe is PANE-relative (desktop's content starts at the rail, phone's is centred); reading it at the screen's own margin was the first version and it panicked desktop and phone on their read-back, which is how the mistake was found rather than shipped.
- **Re-proved on a real boot:** `tools/device_mode_test.sh` PASSES — four modes render, the kernel's read-back verifies the chrome and the shell, all four screendumps differ (`88afac0c cf0ae17c f0856887 5200b869`), selection is stated with its source, the invalid token is named, and the autoload transcript is complete.
- **The style is now MINIMAL, and it is one rule.** Structure is carried by 1 dp hairlines and whitespace instead of filled slabs: `outline()` paints a rounded rect and paints its inside back in the surface colour, and the quick-settings pills, the app icons, the window frame, the taskbar controls and the battery are all that shape. The surface is monochrome with ONE accent (the Start mark), the eight app icons stopped being eight colours (they read as decoration), and the window's minimise/maximise/close are hairlines too.
- **The type ramp is two steps, not four.** `DISPLAY_SCALE` = 21 px is the clock and nothing else; `BODY_SCALE` = 14 px is every label, title and tray item. The face is unchanged and deliberately so: it is the tree's 5x7 bitmap generated from `tools/font5x7.txt`, the same table the display service and the console speak, so a different typeface would be a second font (and a second gate) rather than a style choice. "Lighter" here means the old shell's 42 px display scale is gone, not that the glyphs were redrawn.
- **Everything still inside the HIG numbers:** 8 dp grid, >= 44 dp targets, radii 8/12/16/28, one accent.

**2026-11-20 (follow-on) — the rest of both platforms' elements:**

- **Android 17 side completed.** The status bar now carries the notification DOT beside the clock (a pending notification is a dot, not a count, in Android 17's status bar); quick settings grew from three tiles to four (`INTERNET`, `BLUETOOTH`, `TORCH`, `DO NOT DISTURB`); and the shell draws a notification STACK — card per app with its initial, a detail line and a right-aligned timestamp — below the tiles on the two-column layouts and below the grid on a phone. The clock widget is the at-a-glance element, as before.
- **Windows side completed.** Task View joins the taskbar as two offset hairline squares (deliberately not an app initial: it is not an app), between the running-app buttons and the tray; and the Start menu panel is drawn OPEN above the taskbar under the app window — search field, six pinned apps, a recommended list with timestamps, and the account/power footer behind a hairline divider. The panel exists because a Start button that opens nothing would be the one thing this shell must not pretend to have.
- **Where the space came from.** All of it is inside the pane the modes already reserve: the left column stacks widget -> tiles -> notifications, the right column stacks window -> Start panel. The panel is capped at 300 dp rather than stretched to the pane, because a panel padded out to fill a tall window reads as a bug, not as breathing room.
- **Every element is still one of two primitives** — a 5x7 glyph or a rectangle this file draws — so the union adds no assets, and the single accent is still only the Start mark (plus the one status-bar dot).
- **Re-proved on a real boot after the change:** `tools/device_mode_test.sh` PASSES, four modes distinct (`7fbc0cbc 41e80794 df9fc37a 5200b869`), kernel read-back verified in each, shell container probe intact (the clock widget stays pinned at dp 40 for exactly that reason).

**2026-11-20 (follow-on) — sliders, the tray overflow flyout, and a real lock screen:**

- **Quick settings now carry Android's two sliders** — volume then brightness, the order the shade uses — as hairline tracks with an accent fill to the value and a solid accent thumb at its end. The icons are drawn here (a hairline circle for brightness, a speaker body and two bars for volume); the values are constants like the rest of this renderer's state. Each slider is dropped whole, never clipped, when the height does not fit, and a dropped slider does not hide the cards below it.
- **The Windows tray overflow flyout** is drawn above the tray, right-aligned: the hidden icons plus the row that names the tray. The Start panel yields it the space instead of stacking on top of it — two open surfaces on one edge read as a layering bug — so the panel's block list is now measured from the panel's own height and drops what will not fit. The panel is also capped at 300 dp in both branches: the first version let the flyout's presence stretch it to the pane, which left a void under its last row.
- **The lock screen is a screen, not a panel.** `render_lockscreen` draws the accent lock glyph (hairline shackle, accent body, accent keyhole), the time and date, the notification stack it is hiding, and the gesture hint where the taskbar would be. It is reachable at boot with `nyrqis.lock=1`, which is a boot entry rather than a key because no input reaches the shell yet: the screen can be shown and verified, and unlocking is the part still waiting on a session. `device_mode::render_locked` runs it through the same chrome, layout and read-back contract, and the chrome's bottom-bar probe is INVERTED there — a locked session has no taskbar, so asserting the Start mark's presence in both states would either pass a lock screen that never drew or fail the one that did.
- **The gate grew a fifth boot** (`device_mode_test.sh`: `lock`, cmdline `nyrqis.mode=tablet nyrqis.lock=1`) that reads the lock glyph's accent edge out of the screendump — the same pixel the kernel reads back — and asserts the home screen's Start mark is ABSENT where a taskbar would put it, so "the lock screen replaced the home screen" is a measured fact, not a claim.
- **Re-proved on a real boot:** `device_mode_test.sh` PASSES with the lock case green (`a20bd194`), the four modes still distinct (`30bb0c6c 08c56c24 a472dca1 5200b869`), and every mode's own landmarks intact.

**2026-11-21 — Tide is the shell; the kernel is adapted around it:**

- **The delivered design replaces the shell, rather than being restyled into it.** `porting/release-1-71/shell/src/ui.rs` now draws the Tide layout from the supplied mock: the glass top bar (brand tile, wordmark, mode/identity badge, battery, clock) over a `--line` hairline rule; the keel dock (accent launcher tile, six app tiles with running/active dots) centred on the bottom edge; the launcher lattice (clock, date, search field, eight app tiles with accent initials, recent files); the Notes window with its 36 px title bar, three hairline controls and the snap popover (left / fill / right); the control centre (2x2 quick tiles, brightness then volume sliders, notification list); the console posture (display clock, five card row, hint bar with A/B/Y/HOME); and the phone gesture pill. Palette, radii, spacing and copy are Tide's dark and light tokens, pre-blended to opaque (this renderer cannot blend `rgba()` glass), which is stated at the token table.
- **The kernel moved to it, and it moved in.** `device_mode::content_pane` now hands the shell the WHOLE display in every mode — no rail, status strip, app bar or bottom inset is reserved. A device mode is no longer a frame around the shell; it IS the posture the shell draws (`Posture::Desktop|Tablet|Phone|Console`). `render_with` fills Tide's paper and then calls the shell; the old chrome drawers, the FAB, and the separate console renderer are deleted, not bypassed.
- **The five platform personas fold into Tide instead of painting over it.** `platform_mode::apply` used to paint a second chrome (top strip, dock, taskbar) over the device chrome. It now calls `device_mode::render_identity`, which renders the same Tide screen with the identity's wordmark as the top bar's solid accent badge and its accent as the shell's one accent — one shell, five names and five accents. The lock screen takes no persona: it is the session's face, not an identity's.
- **Probes moved to Tide's facts, in the kernel and outside it.** Kernel and gate both read the top bar's glass at (0,0), the brand tile at (11,17), and one posture element: the keel's padding row `(w/2, h-13)` for desktop/tablet, the gesture pill `(w/2, h-12)` for phone, the console hint bar `(w/2, h-6)` for console. The lock case keeps the glyph's accent edge `(w/2, 66)` and now also asserts the keel is ABSENT. The first tries read the brand tile's centre — which is the apps glyph, not the accent — and the keel at `h-16`, which is a running dot: both failed the boot read-back until the pixels were actually measured, which is why the probe coordinates are now chosen off the drawing code's own numbers.
- **The final frame is announced.** The identity repaint happens after the device-mode verdict, so `lib.rs` prints `screen: settled -- <mode> with identity <name>` once the display stops changing, and the gate waits for that line before screendumping. Before it, the dump photographed a half-painted framebuffer (paper band above a shifted window) and all four modes hashed alike. `QUIET_TIMEOUT` rose 90 -> 180 s for the same reason.
- **Re-proved on a real boot:** `tools/device_mode_test.sh` PASSES — four modes render and verify their own pixels, all four screendumps differ (`b8cb285f 2393926a a776628d 75c6fc5f`), the lock case is green (`a2b3fea7`), the automatic selection and the named invalid token are intact, and the autoload transcript is complete. The host renderer draws the same source at 420x760 and 1024x700/768 for review.
- **Honest substitutions, stated once:** the type is the tree's single 5x7 bitmap (`tools/font5x7.txt`) at 14 px body / 21 px display — Bricolage and IBM Plex from the mock are not in this tree, and a second font would be a second gate. Tide's translucency is pre-blended opaque. Both are limits of this renderer, not of the design.

**2026-11-21 (revision) — the delivered mock itself, 1:1, renamed Nyrqis:**

- **The previous entry ported the design's language; this one ports the mock.** `ui.rs` is now a measurement-for-measurement transcription of the supplied HTML/CSS: the tide-band wallpaper (`paint()`'s gradient plus four sine bands), `#top` with its brand wave, app name, `Window`/`Mode` menus, load-wave, mode chip, battery and clock; `#keel` as a glass pill with launcher tile, hairline separator and six `.tile` app buttons in their own colours (`Terminal #27414f`, `Files #c98512`, `Monitor #0e7d78`, `Notes #2f6fb0`, `Drift #cf4f38`, `Settings #566b79`); `#home .hpanel` with the 40/64 px display clock, date, search field, app lattice, console card row and recent-files list; `.win` with its 36 px `.wbar`, 18 px app icon, three `.wctl` buttons and the 12 px window body; the three start windows (`start()`: notes at 5%/30, terminal at 12%/36%, monitor at max(20,W-620)/46); `#hints` and `#gesture`. Radii, paddings, gaps, font sizes and `rgba()` glass alphas are the stylesheet's numbers.
- **The type is the mock's type.** Bricolage Grotesque (display, 700) and IBM Plex Sans / Mono at their CSS sizes are rasterised by the new `tools/gen_font_atlas.py` into `porting/release-1-71/shell/assets/fonts.bin` (18 faces, 95 glyphs each, 310 KB) and blended per pixel at boot. This is the difference from the first port: the letterforms are Bricolage and Plex now, not the tree's 5x7 table. That table remains only for the kernel's console text.
- **The name is Nyrqis everywhere on screen** -- brand, app-name fallback, the welcome note, the terminal banner, the file paths (`/home/nyrqis`) and the boot console. No "Tide" is rendered.
- **Compositing is real.** The renderer gained source-over blending, so the mock's glass (`rgba(14,30,38,.76)`), hairlines (`rgba(190,225,235,.14)`) and accent-soft chip are composited exactly as the browser does over the wallpaper, rather than pre-blended to a flat token.
- **The lock screen is now a screen the mock actually draws.** The mock has no lock; it has `#boot`, a dark console. `nyrqis.lock=1` now shows that boot console, so the locked state is part of the delivered design instead of an invention beside it.
- **Probes follow the pixels.** The kernel and the gate now read: the top bar's glass composited over the wallpaper's lightest band at (0,0) (computed by the same `overlay()` the renderer uses), the wallpaper's lowest band at (w/2,h-6) (glass over band on console, where `#hints` covers it), the brand wave's accent presence in the bar, a `.wbar` fill on desktop and an app `.tile` colour on the other postures; the lock case reads `#boot`'s surface and its warm line.
- **Re-proved on a real boot:** `tools/device_mode_test.sh` PASSES -- four postures render and read back, screendumps differ (`cde2e27b 07ab3ef2 ad4b9f37 4e306acd`), the boot-console lock case is green, selection/token/autoload cases intact.
- **Limits, stated:** `backdrop-filter` blur is not reproduced (no second off-screen pass; over the smooth wallpaper its absence is invisible), motion/transitions and input are not ported (this is the mock's start frame, not a live session), the SVG icons are redrawn from their viewBox paths with primitives rather than parsed, and the control centre, menus, recents and app-local interactions remain closed/static because the boot frame of the mock shows them closed.

**2026-11-21 (audit) — host render vs QEMU boot frame, and the metrics that were transcribed wrong:**

- **A contact sheet now pairs the two renderers.** `artifacts/shell/contact-sheet-1024x768.png` is the 1024x768 overview; `artifacts/shell/pair-<posture>.png` are the full-resolution sheets (3072x824). Column A is the committed `ui.rs` rendered by the host driver, B is the same audited `ui.rs` exactly as the KERNEL drew it (QEMU screendump from `tools/device_mode_test.sh`), C is `|A-B|` amplified x8. Both columns are the same source, so the sheet measures the renderer against the real framebuffer -- not against an interpretation of it.
- **The one thing the framebuffer and the renderer disagreed on was the accent.** `Platform::Nyrqis`'s palette carried `0x2eeea6` and overrode the shell's `#3cc8c0` on every accent pixel -- 4,232 of them, hard-edged at max delta 38, which is a visible wrong colour on a real panel, not an antialiasing artefact. The Nyrqis identity now uses the shell's own accent and `on_accent` (`0x052321`). After the change the host render and the QEMU frame are **pixel-identical (max delta 0)** in all five captures.
- **`.win`'s title bar erased the window's top border.** `.wbar` was painted across the full 36px box after the 1px ring, so the left, right and bottom edges kept their hairline and the top edge had none (probe: `(250,64)` was bar fill, `(50,200)` was line). The bar is now inset by the border's pixel and the ring is stroked last; both edges read `rgba(190,225,235,.14)` over `--win`.
- **The three `.wctl` glyphs sat 6px high.** A 12px icon centred in the 30x26 button lands at `y+12`, not at the button's top (`y+6`), which is where they were.
- **Glass panels were being filled opaque.** `frame()` paints a ring and then repaints the interior with an opaque fill; used on `#keel`, `#home .hpanel` and the snap popover it turned the glass into glass-over-BLACK (the wallpaper's tide bands disappeared behind the dock) and composited the ring twice (alpha 0.94 against the stylesheet's 0.76). A new `edge()`/`panel()` pair strokes only the 1px perimeter, so the glass keeps the wallpaper behind it, as `rgba()` does in the browser.
- **The dock was 6px too wide and hung off centre.** Its width added a gap after the last tile: 7px padding on the left against 13px on the right, with all six tiles shifted left. `#keel` is 351px now, 7px on both sides.
- **The running/active dot spilled out of the dock.** 4px below a 42px tile plus a 5px height exceeded the 7px bottom padding by 2px. It is 4px tall at a 2px offset, inside the glass.
- **The phone lattice outran its own panel.** Four columns were laid out against `inner = w - 28` while `panel_w` was the 880px tablet panel, so the grid and the right-aligned recent files' column were pushed past the screen edge. The phone is full-bleed like the console (no panel is painted when `full`), and the first/last column margins are 111/112px.
- **Glyph advances were truncated per glyph.** `atype` advanced by `adv/16` for each character, so a 20-character run lost up to a pixel per character relative to the browser's sub-pixel pen; every right-aligned, centred and packed run drifted. The pen now accumulates in 16ths, which is what the browser does.
- **One suspected bug was not one.** `#hints` looked 18px left of centre in the pixel probe, but the old width expression was algebraically identical to the corrected one -- the probe was measuring the accent circles' edges, not the ink box. The bar was, and is, centred; the code is clearer now, nothing else changed.
- **Re-proved on a real boot after the audit:** `./build.sh` succeeds and `tools/device_mode_test.sh` PASSES -- four postures render, verify and differ (`3f31ebee 060f6eed 2de5e61b 23758a4c`), the lock case is green (`50dc7ba9`), selection, rejected-token and autoload cases intact.
- **Still unverifiable until the mock's stylesheet is back in the tree:** the wallpaper's band constants (`base 0.34+i*0.15`, `amp 12+7i`, `f 0.0042/(1+0.18i)`) are approximations of `paint()`; the search field's `min(320)` width cap, the console row drawing 4 of 6 cards without an affordance, the terminal's 8x15 block caret, and the per-widget baseline offsets (`by+5`, `y+11`, `cy+2`) are guesses that only the CSS line-heights can settle.

**2026-11-21 (tablet panel, grid rows, tile highlight, boot log grid):**

- **The tablet posture now paints the panel it was sized for.** `render_named` called `home(..., full = true)` for the tablet, and `home` skips the glass whenever `full` is set while still sizing the lattice and header to the 880px panel -- so the launcher floated bare on the wallpaper with two empty columns of margin and the tide bands running straight through `RECENT FILES` (fg2 over the brightest band measured 2.9:1). The tablet passes `full = false`, and `panel_w` is the 880px panel for every posture that is not phone or full-bleed console (the unreachable 700px desktop branch is gone -- desktop renders windows, not `home`). The panel runs from the same `t + 22` the lattice starts at down to one content gutter above the dock, so the two glass surfaces never overlap.
- **The dock's box is now one function, not two copies of its constants.** `dock_metrics` is what `keel` paints with and what `dock_top` computes the panel's stop from; a 88%-of-content vertically centred panel would have run ~37px under the dock.
- **The tablet lattice is centred.** Eight columns fall out of the 104px minimum for six apps, so the tiles hugged the left gutter (`112..700`) and left a 252px dead band on the right. The row is centred as a block -- `218..806` inside `72..952`, 146px each side. A phone keeps CSS grid's left-aligned partial row (its last row is two of four), which is what the stylesheet's `repeat(4, 1fr)` does.
- **`RECENT FILES` is positioned from the rows the grid drew.** `rec_y = gy + 2*(tile+46) + 10` reserved two rows unconditionally; on the tablet, which draws one row, the heading sat a phantom 151px below the labels. It is `gy + grid_rows*(tile+46) + 10` now, so the phone (two rows) is unchanged and the tablet heading sits directly under the labels.
- **The tile highlight no longer floats off the corners.** `round_a(tile_x+2, cy+1, tile-4, 2, 1, ...)` painted a 2px bar straight across the corner radius: at the 20px corners its ends hung 1-3px outside the fill on a row of background, which is the dashed "hanger" that showed above every icon (and above every dock tile). `top_edge` paints the box's first inside pixel per column, so the highlight follows the radius and is 1px, which is what `inset 0 1px 0 rgba(255,255,255,.28)` is.
- **The panel exposed a compositing bug in the rounded-rect primitive.** `round_a` painted bands plus four corner DISCS; a disc lies inside the box as well as in the corner square, so any translucent fill composited its corners twice. On the new 24px panel that was a visibly darker disc in each corner (measured `(14,34,41)` against `(15,46,52)`). `round_a` is a scanline now -- one inset span per row, measured with the same `inside` that `edge` strokes with, so fill and border cannot disagree by a pixel. Opaque windows and cards are unchanged in shape; the dock's glass corners lose their own double composite.
- **The boot log's columns are a grid, not hand-padded literals.** `compositor` is two characters wider than `init`, so its `ok` sat 23px left of every other row's and the widest labels ran into the figure. `boot_screen` composes each row from its parts: label in columns 0-9, figure right-aligned in 11-12, `MB` at 14, `ok` at 20. Every row's `MB` now inks at 134-147 and its `ok` at 181-194.
- **Re-proved on a real boot:** `./build.sh` succeeds and `tools/device_mode_test.sh` PASSES -- four postures render, verify and differ (`23fd11b4 482ae68c 5723410c 58b06916`), the lock case is green (`bc9b2af1`), auto-selection and the rejected-token cases still select tablet and name the bad token, and the autoload transcript is intact. The regenerated `artifacts/shell/pair-*.png` now show host render and QEMU frame at **max delta 0** in all five captures.
- **The report cannot drift from the layout.** `Info` re-derived its columns from the width (`w / 104` gave the tablet nine while the lattice drew eight) and hardcoded two rows and six tiles for every posture. `home` now returns the lattice it composed -- tablet 8 columns / 1 row / 6 tiles, phone 4 / 2 / 6, console 4 / 1 / 4 (the two cards that do not fit are not counted) -- and the desktop, which arranges windows, reports none. `full` is gone as a parameter as well: it only ever restated the posture, and passing it wrong is what cost the tablet its panel.
- **The rounded-rect primitive is one rule, not two bands plus discs.** `inside` was a middle-band test plus a corner-circle test; it is now the distance into each corner square against the radius, with the same pixel convention, so `round_a`'s scanline, `edge`'s ring and `top_edge`'s highlight all measure the shape one way. The simplification renders **byte-identical** frames in all five captures (same surface hashes before and after: `0xdb9f1474 0x2aa407cd 0xb3c7c1e7 0x3d65f7ce 0x98054fec`).
- **Still guesses until the mock is back in the tree:** the panel's stop (one 22px gutter above the dock) and its top `t + 22`; the centred tablet row (the stylesheet may centre the grid, or use fewer, wider columns); the 1px highlight height; and the boot log's exact column widths.

## The Win32 API surface, v0: four kernel32 exports, performed by the kernel (2026-11-21)

The repo's own scope notes call real Windows application compatibility
its largest gap, and this increment does **not** close it. What it
closes is the specific mechanical gap named in every one of those
notes: "no Win32 API surface". Four Win32 functions now exist, done
the way a real one has to be done -- a Windows-format DLL whose export
bodies are ring-3 trap stubs, a Windows-format console program that
imports them the ordinary way, and a kernel that performs each
operation and hands back a Windows-typed result.

- **The provider is a real DLL, not a shim in the kernel's own image.**
  `userprog/prog_win32_kernel32.s` links to
  `userprog/kernel32.dll` (`ld -m i386pep --shared --enable-auto-import
  --image-base=0x190000000000`), declaring the DLL name `kernel32.dll`
  and exporting exactly `GetStdHandle`, `WriteFile`, `GetTickCount`,
  `ExitProcess` (RVA `0x1000`, `0x101c`, `0x104d`, `0x1062`). The
  client's import descriptor is synthesized by GNU `ld` itself from
  that DLL -- nothing in this repo hand-writes an import directory for
  this link.
- **The arguments travel in a fixed call block**, because an `int` gate
  saves no general-purpose registers (the CPU pushes only RFLAGS, CS,
  RIP, RSP and SS on a privilege change). Each stub parks RCX/RDX/R8/R9
  in `.data` (RVA `0x2000`, magic `0x4e59525733325f31`), writes its own
  function index at `+48`, traps `int $0x90`, reads the kernel's result
  back from `+40` into RAX and returns. The kernel pins those addresses
  (`experimental::win32::{TEXT_RVA, BLOCK_RVA, BLOCK_MAGIC}`) and the
  build refuses a fixture whose `.text`/`.data` moved -- checked with
  `objdump -h` inside `build.sh`, so layout drift fails at link time
  rather than becoming a kernel reading whatever now lives at the old
  RVA.
- **Nothing ring 3 says is trusted, and the evidence is typed.** The
  block is ring-3-writable, so `win32::dispatch` checks: the index is
  one of the four; the trapping RIP lies inside kernel32's own `.text`
  window (`+0x1000..+0x1098`) -- the index is a claim, the address is
  evidence, so a client cannot fake a kernel32 call by trapping
  directly; `WriteFile`'s handle is the one `GetStdHandle` issued
  (`0xb`); its buffer and out-parameter lie inside the client image or
  the client's private report page; its length is under the surface's
  cap (96 bytes); and its bytes are text. `GetStdHandle` reads its
  argument as the `DWORD` Windows declares it to be -- the first boot
  of this link refused its own valid `GetStdHandle(STD_OUTPUT_HANDLE)`
  because the stub parks the full RCX and a C caller only ever sets
  the low half, which is exactly the kind of mistake a boot transcript
  catches and a design document does not.
- **`GetTickCount` is honest about its clock.** It is
  `idt::timer_ticks() * 55`: this kernel's PIT runs at its default
  ~18.2 Hz, so the value advances in 55 ms steps. Two calls inside one
  tick return the same number, and the proof asserts monotonicity
  rather than pretending to millisecond resolution.
- **Five refusals are exercised every boot, kernel-side, before ring 3
  runs** (index 9, a trap at `kernel32+0x8`, an unissued handle, a
  buffer at `0x10`, a 97-byte length, an out-parameter at `0x10`), and
  the client's own run must raise none -- the self-test prints the
  count.
- **Proved on a real boot, transcript kept:** `kernel32.dll` loaded at
  its own ImageBase with its export table declaring `kernel32.dll` and
  four names all inside `.text`; the call block's magic found at
  `+0x2000`; two typed negative refusals of the import walk (empty
  registry -> `MissingDllImport`, kernel32's name bound to the client's
  own empty export table -> `MissingImport`); four imports resolved
  against the client's REAL `.idata` with every slot reading back its
  export address; then, in ring 3,
  `WriteFile returned 1 and wrote 44 byte(s)`, the console received
  `nyrqis: hello from a Win32 console program\r\n`, `GetTickCount`
  read `205975` then `205975` (monotonic), `ExitProcess recorded
  Some(7)`, `refusals 0`, and the existing chain continued through
  `isolation_demo` to a clean guest exit (`isa-debug-exit` code 33).

**Stated limits, not hidden ones.** Four functions out of the tens of
thousands kernel32 exports; no NT system-call layer beneath them (no
`NtWriteFile`, no object manager, no handles table -- `0xb` is a
constant this surface issues and accepts); no user32/gdi32/kernelbase;
no Microsoft code; `ExitProcess` records its code and returns, because
v0 has no task teardown to switch to -- the client's next instruction
is this link's own completion trap, which asserts the recorded code;
and the client's console output goes to the serial line, which is this
headless kernel's console, not a windowed terminal.

**Gate note.** `tools/qemu_boot_test.sh` pins three of these lines. Its
120 s wall-clock cap turned out to be too short for a shared cloud VM
under pure TCG: the same ISO timed out mid-boot with and without this
increment (verified by stashing the change and re-running), so the cap
-- not this increment -- was what failed the gate. The cap is now
600 s (`timeout 600`), with the measurement and the reasoning recorded
in the script next to it, and the gate is green end to end on this
increment: `PASS: kernel reached BootPhase::Ready and exited cleanly`,
`GATE_EXIT=0`, with all three new win32 lines matched in that same run
(`resolved 4 import(s) ... -- OK`, `-Werror`-clean build, and the
`WriteFile returned 1 and wrote 44 byte(s) ... refusals 0` self-test).
A hang still fails the gate; it just now takes longer than this host
needs (measured ~3.5 minutes for the whole boot leg).

## The Win32 API surface v0.1: files, and the read-only volume behind them (2026-11-22)

The v0 tranche above proved the mechanism with four console functions.
This increment adds the three calls a program needs before the console
matters -- `CreateFileA`, `ReadFile`, `CloseHandle` -- and the smallest
thing they can act on: `experimental::win32_fs`, a read-only in-memory
volume of three files in the kernel image (`C:\nyrqis\hello.txt`,
`C:\nyrqis\bytes.bin`, `C:\nyrqis\empty.txt`).

**Why a separate module.** `win32` owns Win32 *semantics* (which
arguments are legal, what each refusal is called, what a Windows-typed
result looks like); `win32_fs` owns *state* (which files exist, which
handles are open, where each read cursor is). The dispatcher never
reaches into a file table and the volume never has to know what an
`int $0x90` is.

**What the volume decides, stated.** A path must be fully qualified
(`C:\...`): a relative path is refused, because there is no current
directory to resolve it against and guessing one would be a silent lie.
Only `C:` is mounted, so `D:` is a typed `UnknownVolume`. Comparison is
ASCII case-insensitive with `\` and `/` interchangeable, which is what
Win32 path parsing and NTFS do. `CreateFileA` hands out `0x100 + 4*slot`
for the first FREE slot of four, so a handle is a capability: a value
the volume never issued is `NotAFileHandle`, a value it issued and then
closed is `ClosedHandle`, and slots are reused in order after a close --
the boot asserts the reuse by value (`0x100` three times) rather than
assuming it. `ReadFile` is byte-oriented (no text decoding, no
translation), advances the cursor, and reports EOF as a successful read
of zero bytes, as Windows does.

**Three things the first two boots of this link caught, kept here
because the transcript is the point.** (1) The volume's negative
controls wrote their paths into the client's report page, but that page
was mapped further down the function -- a page fault at
`0x4d0000001000`, which is what happens when a "harmless" assertion
runs before the mapping it needs. The mapping now happens before any
control, with a comment saying why. (2) The helper that NUL-terminates
those paths appended the NUL past the last byte of the page -- a second
fault, at exactly one byte beyond the mapping, in the control that
proves an unterminated path is refused at the region's edge. That one
byte is now written without a terminator, on purpose. (3) The first run
of the client itself failed `refusals 0`: the program deliberately uses
a closed handle twice, and the surface correctly refused both. The
expectation is now `EXPECTED_REFUSALS = 2` with the reason written
beside it, instead of an assumption that a real program never probes
an error path.

**The fifth argument, and the word that was reserved for it.** V0's
`+56` was reserved so the block's `0x40` size (and its RVA pin) could
not move. `CreateFileA` and `ReadFile` both declare five parameters, and
Microsoft's x64 convention passes the fifth on the stack, so those two
stubs now fetch `0x28(%rsp)` and park it at `+56`: the reserved word
became load-bearing without a layout change, and the kernel can refuse a
non-NULL `lpOverlapped` and a `dwCreationDisposition` other than
`OPEN_EXISTING` instead of silently dropping what the client asked for.

**The same DWORD lesson, twice more.** `GetStdHandle` already had to
read its argument as the 32-bit value Windows declares; `CreateFileA`'s
`dwDesiredAccess` is now read the same way (a C caller sets only `EDX`),
and both out-parameters (`lpNumberOfBytesWritten`,
`lpNumberOfBytesRead`) are written as a `DWORD`, not a qword. The first
version of `WriteFile` wrote eight bytes into a four-byte out-parameter,
which would have clobbered the variable next to it in any real client;
that is fixed here rather than carried forward.

**Proved on a real boot, transcript kept** (`./build.sh --qemu-test`
green, then `tools/qemu_boot_test.sh` -> `GATE_EXIT=0`):

```
experimental win32-demo: kernel32.dll loaded at its own ImageBase 0x190000000000; export table declares "kernel32.dll" with 7 name(s), all inside .text (+0x1000..+0x1130); call block magic 0x4e59525733325f31 at +0x2000 -- OK
experimental win32-demo: resolved 7 import(s) from the client's REAL .idata against kernel32.dll's REAL export directory (GetStdHandle, WriteFile, GetTickCount, ExitProcess, CreateFileA, ReadFile, CloseHandle), every slot reading back its export address -- OK
experimental win32-demo: negative control 6 -- the read-only volume refused a relative path (PathNotAbsolute), drive D: (UnknownVolume), a control byte in a path (PathNotText), a path with no NUL inside the client's page (PathTooLong), a pointer outside the client (PathOutsideClient), GENERIC_WRITE (UnsupportedAccess), disposition 2 (UnsupportedDisposition), a non-NULL lpOverlapped (OverlappedNotSupported), the console handle as a file (InvalidHandle / NotAFileHandle) and a 5th open (OutOfHandles); a missing file returned INVALID_HANDLE_VALUE as Windows does, and every handle was closed again -- OK
nyrqis: hello from the read-only volume
experimental win32-demo self-test: WriteFile returned 1 and wrote 33 byte(s); the console received "hello from the read-only volume\x0d\x0a"; hello.txt opened as handle 0x100, read 5+28 byte(s) then 0 more at EOF, closed -> 1 and the client's own copy of the bytes matches; bytes.bin read back byte-identically (not text: 0x00/0x80/0xfe), empty.txt read TRUE with 0 byte(s); a closed handle was refused by ReadFile (0) and by a second CloseHandle (0); a missing file returned 0xffffffffffffffff; GetTickCount read 226160 then 226160 (monotonic); ExitProcess recorded Some(7); 0 file handle(s) still open; refusals 2 (the program's two deliberate uses of a closed handle) -- OK, a real Windows-format console program called seven kernel32 exports through a real PE32+ DLL's export table and the kernel performed them in ring 0 against a read-only in-memory volume -- a real Win32 API surface, not Windows compatibility (no NT syscall layer, no directories or writes, ExitProcess records and returns)
```

The gate pins six of those lines by substring (`resolved 7 import(s)`,
`negative control 6`, the self-test's `wrote 33 byte(s)`,
`bytes.bin read back byte-identically`, `empty.txt read TRUE with 0
byte(s)`, `a missing file returned 0xffffffffffffffff`), so the file
tranche cannot silently stop running while the boot still exits 33.

**Stated limits, not hidden ones.** Read-only: there is no write path, so
`CreateFileA` refuses `GENERIC_WRITE` and there is no `WriteFile` to a
file. Three files, no directories, no current directory, no dates, no
security descriptors, no sharing semantics (the share mode is accepted
and ignored, and the surface says so). Four handles. Synchronous only:
`lpOverlapped` must be NULL. `GetLastError`/`SetLastError` do not exist
yet, so a client that sees `INVALID_HANDLE_VALUE` cannot ask *why* --
that is the next thing a real binary would need, and it is missing.
Handle values are this surface's own, not NT's. And all of the v0 limits
still stand: no NT system-call layer, no user32/gdi32, no Microsoft
code, `ExitProcess` records and returns, and the console is the serial
line.

## The Win32 API surface v0.2: GetLastError, and the codes behind every refusal (2026-11-22)

The v0.1 section above ends by admitting the gap this increment closes:
a client that receives `INVALID_HANDLE_VALUE` could not ask *why*.
`GetLastError` and `SetLastError` now exist, and -- this is the part
that makes the pair worth having -- the codes they answer come from the
same typed refusals the surface already raises.

**One table, not a number at each call site.** `Win32Error::code()` maps
every variant (and every `FsError` inside `Win32Error::File`) to the
value Windows itself uses: `ERROR_INVALID_FUNCTION` (1) for an index no
export answers, `ERROR_FILE_NOT_FOUND` (2), `ERROR_PATH_NOT_FOUND` (3),
`ERROR_TOO_MANY_OPEN_FILES` (4), `ERROR_ACCESS_DENIED` (5),
`ERROR_INVALID_HANDLE` (6), `ERROR_INVALID_DRIVE` (15),
`ERROR_NOT_SUPPORTED` (50), `ERROR_INVALID_PARAMETER` (87),
`ERROR_INVALID_NAME` (123), `ERROR_FILENAME_EXCED_RANGE` (206),
`ERROR_INVALID_ADDRESS` (487) and `ERROR_NOACCESS` (998). A code written
next to the `return Err(..)` that raised it drifts the moment a check is
reused, and the drift is invisible because the client still gets a
plausible number; deriving it from the variant means a refusal can only
report the code of the condition it actually detected.

**Two kinds of failure, kept distinct.** A REFUSAL (the call was not
performed) records its code and returns `FALSE`/0 to the stub. A
Windows-style FAILURE VALUE is not a refusal but still a failure:
`CreateFileA` on a missing file returns `INVALID_HANDLE_VALUE` *and*
records `ERROR_FILE_NOT_FOUND`, which is the exact case the boot
client asks about. A SUCCESSFUL call leaves the cell alone, as Windows
does -- the code is only meaningful immediately after a failure, and a
surface that cleared it would hide a client checking in the wrong place.
`dispatch` is the single gate that records, so no path can refuse
without its code being stored (the boot's kernel-side controls go
through it too, which is how they assert the mapping).

**Proved on a real boot, transcript kept** (`./build.sh --qemu-test`
green, then `tools/qemu_boot_test.sh` -> `GATE_EXIT=0`):

```
experimental win32-demo: resolved 9 import(s) from the client's REAL .idata against kernel32.dll's REAL export directory (GetStdHandle, WriteFile, GetTickCount, ExitProcess, CreateFileA, ReadFile, CloseHandle, GetLastError, SetLastError), every slot reading back its export address -- OK
experimental win32-demo: negative control 7 -- the last-error cell started at ERROR_SUCCESS (0), every one of the 19 typed refusals answered its pinned Windows code (unknown function 1, trap outside .text 487, invalid handle 6, out-of-region buffer/out-parameter/path 998, oversized length 87, unterminated path 206, non-text path 123, write access 5, bad disposition 87, overlapped I/O 50, relative path 3, unknown drive 15, missing file 2, exhausted table 4), a successful open left the code alone, and SetLastError(0x1234) read back through GetLastError -- OK
experimental win32-demo self-test checks: console=true hello=true bytes=true empty=true closed=true miss=true errors=true monotonic=true exit=true handles=true refusals=true; handles f1=0x100 f2=0x100 f3=0x100 f4=0x100 f5=0xffffffffffffffff f6=0x0; closes 1 1 1 1; reads 1/1/1/1/1/0
experimental win32-demo self-test: ... a missing file returned 0xffffffffffffffff and GetLastError answered ERROR_FILE_NOT_FOUND (2), the closed handle answered ERROR_INVALID_HANDLE (6 then 6), GENERIC_WRITE was refused with ERROR_ACCESS_DENIED (5), SetLastError(0x1234) read back as 0x1234, and a successful GetTickCount left the code at 5; ... refusals 3 (the program's closed-handle pair and its refused write-access open) -- OK, a real Windows-format console program called nine kernel32 exports ...
```

The gate pins the count (`resolved 9 import(s)`), control 7, the three
codes the client reads back, and now the self-test's own `-- OK`
verdict, so a MISMATCH line cannot satisfy the gate.

**Three things the boots of this link caught, kept because the
transcript is the point.** (1) The first boot after adding the two
exports died with a `DOUBLE FAULT` and no panic message at all. The
frame put RSP page-aligned in `kernel_stack`'s region: this stage had
never run on its own stack, it rode the inherited one (already carrying
every earlier demo's frames), and the tranche's few hundred bytes
crossed the guard page. The guard did its job -- zero corruption, no
guesswork -- and the fix is the rule every sibling follows: the win32
stage now allocates its space with `WIN32_STACK_PAGES = 8` and jumps
onto it with `chain::run_on_fresh_stack`. (2) The next boot printed a
perfect console line and still said MISMATCH: the new report fields were
numbered from `+208` and the last landed on `+0x100`, on top of the
buffer holding the file bytes the client writes to the console -- the
console write had already copied them, the read-back had not. They are
now at `0x210..0x240`, and a `const` assertion refuses at COMPILE time
any layout where they overlap the read buffers or the `0x300` control
paths. (3) The gate passed that MISMATCH boot, because every needle was
a substring of a line that was present either way; the `-- OK` verdict
is a needle now.

**Stated limits, not hidden ones.** The last error is one cell for the
kernel, not one per thread: Windows keeps it in a TEB this kernel does
not have, and v0 has a single ring-3 client. Codes exist only for the
conditions this surface can detect -- there is no `FormatMessage`, no
`ERROR_*` catalogue beyond the 19 rows the refusals produce, and
`SetLastError` accepts any DWORD. All v0/v0.1 limits stand: no NT
system-call layer, no directories or writes, four handles, synchronous
I/O only, `ExitProcess` records and returns, and the console is the
serial line.

## The Win32 API surface v0.3: a refused call returns what Windows returns (2026-11-22)

v0.2 made every refusal explainable through `GetLastError`. It left the
other half of the pair wrong: the RESULT. A refused call answered 0 for
every export, so a client that opens a file and tests the answer the way
Windows' prototype tells it to --

```c
HANDLE h = CreateFileA(...);
if (h == INVALID_HANDLE_VALUE) { DWORD why = GetLastError(); ... }
```

-- would have compared 0 against all-ones, concluded the open had
succeeded, and gone on to `ReadFile` a handle that was never issued.
This increment returns the failure value of the export's own return
type instead: `INVALID_HANDLE_VALUE` from a refused `CreateFileA` and
`GetStdHandle` (both `HANDLE`), 0 from `WriteFile`, `ReadFile` and
`CloseHandle` (`BOOL`), 0 from `GetTickCount` and `GetLastError`
(`DWORD`) and from `ExitProcess` / `SetLastError` (`void`, or never
returning). An index that names no export has no prototype to borrow
from and answers 0.

**Two tables, because they answer two questions.** `Win32Error::code()`
answers *why* and depends on the condition that refused; the new
`win32::failure_value(index)` answers *what did the call return* and
depends only on the function. Merging them would force a code-shaped
result, and keying the value on the error would mean a `CreateFileA`
refused for a relative path returned 0 while one refused for write
access returned all-ones -- neither of which any Windows client can
reason about. The value is applied in exactly one place, `service`, the
`int $0x90` handler, where a refused dispatch's typed error has already
been recorded; `dispatch` keeps returning `Err` so the kernel-side
controls still assert the reason.

**Proved in the same boot gate, with the pins updated.** The client asks
for `GENERIC_WRITE` on the read-only volume as before; the refusal now
returns `INVALID_HANDLE_VALUE`, and the client performs Windows' own
test on it (`cmpq $-1`) and stores the verdict, so the transcript shows
the client -- not the kernel -- reading the value. A new kernel-side
control pins the value for all nine exports plus an index outside the
table, so an export added with the wrong row fails the boot.

```
experimental win32-demo: negative control 8 -- a refused call answers with its export's own Windows failure value: INVALID_HANDLE_VALUE from GetStdHandle and CreateFileA (HANDLE), 0 from WriteFile, ReadFile and CloseHandle (BOOL), 0 from GetTickCount and GetLastError (DWORD), 0 from ExitProcess and SetLastError (void), and 0 from an index that names no export -- OK, so the client's handle test reads a refusal exactly as Windows writes it
experimental win32-demo self-test checks: console=true hello=true bytes=true empty=true closed=true miss=true errors=true monotonic=true exit=true handles=true refusals=true handle_test=1; handles f1=0x100 f2=0x100 f3=0x100 f4=0x100 f5=0xffffffffffffffff f6=0xffffffffffffffff; closes 1 1 1 1; reads 1/1/1/1/1/0
experimental win32-demo self-test: ... GENERIC_WRITE was refused with ERROR_ACCESS_DENIED (5), returned 0xffffffffffffffff rather than 0 and passed the client's own INVALID_HANDLE_VALUE handle test (1), SetLastError(0x1234) read back as 0x1234 ... -- OK, a real Windows-format console program called nine kernel32 exports ...
```

The gate pins control 8, the handle-test verdict and the self-test's
`handle_test=1` field, alongside the existing `resolved 9 import(s)`,
control 7, code and `-- OK` needles. `f6` in the checks line moved from
`0x0` to `0xffffffffffffffff`, which is the change stated in one number.

**Limits.** The mapping is a claim about the declared return type, not
about Windows' internals: it does not model `GetLastError`'s
"no error is not success" subtlety beyond leaving the cell alone, it
does not translate NT `STATUS_*` codes, and it does not make an
unissued handle safe to use -- the refusal is still counted and still
reported. Every v0/v0.1/v0.2 limit stands.

## The Win32 API surface v0.4: the startup pair a CRT calls first (2026-11-22)

v0.3 made a refusal read the way Windows writes one. It still left a
program unable to START the way a Windows program does: a CRT's first
two calls are not console or file calls at all, they are
`GetCommandLineA()` and `GetModuleHandleA(NULL)` -- where was I told to
go, and where am I? Without them a client can only be a hand-written
stub that already knows both answers, which is exactly the crutch the
previous three tranches were supposed to remove. Both are now exports
of the same `kernel32.dll` stub table (eleven, up from nine), and both
are asked from ring 3 by the client in the boot proof, in the order a
CRT asks them.

**Loader state, not constants -- which is the whole point.** Neither
answer is about the call; both are about the PROCESS. So neither is
computed here:

- `GetCommandLineA` returns the pointer the new
  `win32::install_cmdline` recorded after writing the string into the
  client's own mapped page. A kernel static would be the obvious
  shortcut and would be a lie: its page is supervisor-only, so a ring-3
  CRT reading the returned `LPSTR` would fault on an address this
  surface told it to use. The install also refuses, by name, text that
  is not printable ASCII, and a string that runs past the client's
  region -- the one failure mode the client could not diagnose for
  itself. Two calls answer the same address, so a CRT may cache it, as
  on Windows.
- `GetModuleHandleA(NULL)` returns the base the client was actually
  mapped at, which the surface already owns from `install`. The client
  proves it is not a constant by checking its own `_start` lies inside
  `[hmod, hmod + SizeOfImage)`.
- Both refuse with the new `LoaderStateMissing` (`ERROR_NOT_READY`)
  when the loader state does not exist yet, instead of answering `Ok(0)`
  -- a starting CRT handed a NULL would dereference it. The proof's
  control fires that refusal BEFORE the install, so the ordering is a
  fact of the boot rather than a claim about a branch.
- A NAMED module is refused with `ModuleNameNotSupported`
  (`ERROR_MOD_NOT_FOUND`): v0 has no module table, and "that module is
  not loaded in this process" is exactly true. The client asks only the
  NULL form, which succeeds.

**The failure-value table was keyed on the wrong thing, and this
tranche is where that shows.** v0.3 mapped "HANDLE-returning export" to
`INVALID_HANDLE_VALUE`. `GetModuleHandleA` returns `HMODULE` -- a handle
type -- but its documented failure is NULL, and `GetCommandLineA`
(`LPSTR`) also fails with NULL. Returning all-ones as a module base
would send a CRT walking address `0xffffffffffffffff` for its own
headers. So the row is now "the failure result the prototype
DOCUMENTS", both new exports answer 0, and control 8 pins that row
beside the two that really do answer `INVALID_HANDLE_VALUE`.

**Three new typed refusals, one new table row each.** `Win32Error`
grew `LoaderStateMissing` (21), `ModuleNameNotSupported` (126) and
`CommandLineInvalid` (87, the loader-side argument check), so the
last-error table control went from 19 pinned rows to 22 -- adding a
variant without deciding its Windows code still fails the boot.

**Pins moved, deliberately, and the build refuses the drift first.**
Two stubs appended for a stable layout: `.text` `0x158` -> `0x188`
(`TEXT_SIZE`, and the `build.sh` objdump gate that checks it),
`GetCommandLineA` at `0x1137`, `GetModuleHandleA` at `0x114c`; the call
block, the DLL `ImageBase` and the client's `SizeOfImage 0x4000` are
unchanged. Because the index space is now `0..=10`, the
`UnknownFunction` control moved from index 9 to index 11 -- a
negative control that still asked index 9 would now be answered by
`GetCommandLineA` and would have silently stopped testing anything.

**Proved from both sides in the same boot.** Kernel-side, control 9
pins the refusal before the install, both bad installs, pointer
stability across two calls, the bytes plus their NUL at the answered
address, the module base, and the named-module refusal. From ring 3 the
client compares the string at the returned pointer byte-for-byte
against its own copy (NUL required), re-asks for the pointer and
compares the two, and tests the module handle against its own `_start`.

```
experimental win32-demo: resolved 11 import(s) from the client's REAL .idata against kernel32.dll's REAL export directory (GetStdHandle, WriteFile, GetTickCount, ExitProcess, CreateFileA, ReadFile, CloseHandle, GetLastError, SetLastError, GetCommandLineA, GetModuleHandleA), every slot reading back its export address -- OK
experimental win32-demo: negative control 9 -- GetCommandLineA was refused with the typed LoaderStateMissing before any loader placed a string; a non-text install and one running past the client's page were refused as CommandLineInvalid and published nothing; the real install then answered the SAME pointer twice (0x4d00000002a0) with "C:\nyrqis\win32_console.exe" plus its NUL readable there; and GetModuleHandleA(NULL) answered the client's own image base 0x1a0000000000 while a named module was refused as ModuleNameNotSupported -- OK, the startup pair reads loader state rather than inventing it
experimental win32-demo self-test checks: console=true hello=true bytes=true empty=true closed=true miss=true errors=true monotonic=true exit=true handles=true refusals=true handle_test=1 startup=true; handles f1=0x100 ...
experimental win32-demo self-test: GetCommandLineA answered 0x4d00000002a0 on both calls with the client's own byte-and-NUL compare 1 it, and GetModuleHandleA(NULL) answered the client's image base 0x1a0000000000 with the client's _start-inside-image test 1 it; WriteFile returned 1 and wrote 33 byte(s); ... -- OK, a real Windows-format console program started the way a CRT does -- its command line and its own image base, both read from loader state -- then called eleven kernel32 exports ...
```

The gate pins control 9, the `startup=true` field, and both ring-3
clauses, alongside the moved `resolved 11 import(s)` and `-- OK`
needles. The client's own run still raises exactly three refusals -- the
closed-handle pair and the refused `GENERIC_WRITE` open -- so the new
calls add no refusals of their own: they are the first successful calls
in the transcript.

**Limits.** The command line carries no arguments (nothing passes any)
and there is no environment block: `GetEnvironmentStrings`,
`GetCommandLineW`, `GetStartupInfo` and `__getmainargs` are not here,
so a real CRT's startup would still stop at the third call. There is no
module table, so a named `GetModuleHandleA` is refused rather than
resolved, and no `GetProcAddress`. Every v0/v0.1/v0.2/v0.3 limit stands
unchanged.

## The Win32 API surface v0.5: GetProcAddress and a real module table (2026-11-22)

v0.4 let a program START the way a Windows program starts: it could ask
where its image is and what it was told on the command line. It could
not BIND. Every address it used had been patched into its IAT before
its first instruction, which makes its imports a link-time fact -- and
that is not how a Windows program behaves the moment it does anything
interesting: a plugin, a late-loaded library, or a CRT resolving its own
helpers by name all ask a module for an export AFTER the loader is done.
The gap was visible in v0.4 itself: `GetModuleHandleA("kernel32.dll")`
could only be refused, because a name had nothing to resolve against.

So this tranche adds the other half of that pair -- `GetProcAddress`,
twelfth export of the same `kernel32.dll` stub table -- and the thing it
needs to be honest: a MODULE TABLE filled by the loader, one row per
module it mapped, each row carrying that module's own parsed
`IMAGE_EXPORT_DIRECTORY`. A named `GetModuleHandleA` now resolves
(case-insensitively, as Windows compares a file name), and
`GetProcAddress` answers out of the row's real export table rather than
out of a list kept beside the dispatcher.

**Why the table, and not a name->address list.** The cheapest way to
pass a boot proof here would have been a `match name { b"GetTickCount"
=> 0x19000000104d, ... }` inside `win32`. It would answer correctly for
this fixture and prove nothing: it would answer just as correctly for a
DLL that exported nothing, and the client's comparison against its
loader-bound IAT slot would still pass, because both sides would be
reading the same hand-typed number. Answering from the EAT means the
answer is the FILE's claim: `DllExportTable::lookup` walks the mapped
image's name pointer table, ordinal table and export address table, so a
name resolves exactly when the loaded provider says it does. The proof
is then structural rather than textual: a `(name, address)` literal has
nothing to disagree with, and an EAT lookup disagrees the moment the
fixture's table and the kernel's idea of it drift.

**Why a row is certified at install time.** `install_module(name, base,
image_size, exports)` is loader-side code, so a wrong argument is a bug
here -- but one of those bugs has teeth. `lookup` returns an RVA the
export header merely POINTS AT; it never asks whether that RVA is inside
the image. For the import walk that was tolerable (a wrong address
traps in the client, where the crash is attributable); once an address
is handed to ring 3 as a function to CALL, an uncertified EAT entry is a
pointer into whatever is mapped next, kernel text included. So the new
`pe::DllExportTable::every_export_within` walks every EAT slot once, and
a row that fails it is refused WHOLE (`ModuleInstallInvalid`) instead of
being published and re-checked on every call. A refused registration
leaves the table exactly as it was, so the rows a client's calls see are
always certified rows.

**Two comparison rules, deliberately different.** Module names are
compared ASCII case-insensitively; export names byte-exactly. That
asymmetry is Windows': `GetModuleHandleA("KERNEL32.DLL")` and
`("kernel32.dll")` name one module because a module name is a file name,
while `GetProcAddress(h, "gettickcount")` fails because a PE export name
is a string compared exactly. Both rules are pinned -- the kernel-side
control asks the uppercase spelling and requires the same base, and asks
an export name in the wrong case and requires `ERROR_PROC_NOT_FOUND` --
because a description of an asymmetry is worth nothing if only one half
of it is exercised.

**Seven new typed refusals, each with the code Windows uses for that
condition.** `ModuleNotFound` (126) is "that module is not loaded in this
process" and `ProcNotFound` (127) is "that module has no such export" --
two different questions a client must be able to tell apart, because one
means it named the wrong library and the other the wrong function.
`ModuleNotLoaded` (6) is a base that is not a row -- the same
`ERROR_INVALID_HANDLE` every other unissued handle answers.
`ModuleTableFull` (14) is the table's cap: a resource condition, not a
bad argument. `ModuleInstallInvalid` (87) is an unusable row (empty,
non-text or over-long name; duplicate; zero base; zero image; an export
outside the image). `NameInvalid` (87) is a name argument ring 3 made
unreadable (NULL, outside its pages, not text, unterminated) -- one
variant where a path has three, because for a NAME there is only one
thing the client can do about it. `OrdinalNotSupported` (50) is
`GetProcAddress` in its low-word-ordinal form: `ERROR_NOT_SUPPORTED`
says "this request form is not served", which is true, rather than
`ERROR_PROC_NOT_FOUND`, which would claim the ordinal does not exist.
The table `GetLastError` answers from is now 28 rows, and control 7
pins every one of them as a literal.

**Pins moved, deliberately, and the build refuses the drift first.**
`GetProcAddress` is appended last in the fixture so every earlier RVA
stayed put: `.text` `0x188` -> `0x1b0` (`TEXT_SIZE`, and the `build.sh`
objdump gate that checks it), the new stub at `0x1168`, the DLL's
`ImageBase`/call block and the client's `SizeOfImage 0x4000` unchanged.
The client's import count is now twelve, and because the index space is
now `0..=11` the `UnknownFunction` control moved from index 11 to index
12 -- a control that still asked 11 would now be ANSWERED by
`GetProcAddress` and would have silently stopped testing anything. The
client's report page grew nine fields, and the compile-time assert that
keeps them from overlapping the read buffers, the installed command
line, the `0x300` control-path area or each other was tightened to
cover the new ranges -- the collision it guards against really happened
once (see the v0.3 section) and produced a green-looking console line
beside a failed read-back.

**Proved from both sides in the same boot.** Kernel-side, control 10
walks the whole table surface before ring 3 runs: an empty table (where
"nothing loaded" and "no such module" must be different answers), five
unusable registrations, a duplicate row, the two-row cap, all twelve
exports BY NAME, the wrong-case export name, the client's exportless
image, an unregistered base, the ordinal form and a NULL name. From
ring 3 the client asks what a real program asks: `GetModuleHandleA`
by name in both cases, `GetProcAddress` for `GetTickCount`, a compare
of that answer against the IAT slot the loader filled (two independent
paths to one export), and then a CALL through the pointer it was
handed -- so the runtime-bound address is proved callable, not merely
equal. It also asks for a module that is not loaded and an export that
module does not have, and reads back 126 and 127.

```
experimental win32-demo: resolved 12 import(s) from the client's REAL .idata against kernel32.dll's REAL export directory (GetStdHandle, WriteFile, GetTickCount, ExitProcess, CreateFileA, ReadFile, CloseHandle, GetLastError, SetLastError, GetCommandLineA, GetModuleHandleA, GetProcAddress), every slot reading back its export address -- OK
experimental win32-demo: negative control 10 -- with no module registered, GetProcAddress was refused as LoaderStateMissing and a named GetModuleHandleA as ModuleNotFound; five unusable rows (empty name, non-text name, zero base, zero image, an image too small for the exports it names) were refused as ModuleInstallInvalid and published nothing; kernel32.dll and win32_console.exe then registered, both spellings of the provider's name answered 0x190000000000, a duplicate and a third row were refused (ModuleInstallInvalid, ModuleTableFull at 2 rows), all 12 exports resolved BY NAME from the provider's own export directory, a lowercase export name and the client's exportless image answered ProcNotFound, an unregistered base ModuleNotLoaded, an ordinal request OrdinalNotSupported and a NULL name NameInvalid -- OK, GetProcAddress answers from a module table the loader built and then certified
experimental win32-demo self-test checks: console=true hello=true bytes=true empty=true closed=true miss=true errors=true monotonic=true exit=true handles=true refusals=true handle_test=1 startup=true bindings=true; handles f1=0x100 f2=0x100 f3=0x100 f4=0x100 f5=0xffffffffffffffff f6=0xffffffffffffffff; closes 1 1 1 1; reads 1/1/1/1/1/0
experimental win32-demo self-test: GetCommandLineA answered 0x4d00000002a0 on both calls with the client's own byte-and-NUL compare 1 it, and GetModuleHandleA(NULL) answered the client's image base 0x1a0000000000 with the client's _start-inside-image test 1 it; GetModuleHandleA("kernel32.dll") answered the provider's own base 0x190000000000 in either case, and GetProcAddress named GetTickCount at 0x19000000104d -- the very address the loader bound into the IAT (1) -- which the client then CALLED through, reading 220055 (>= 220055); a module that is not loaded answered 0x0 with ERROR_MOD_NOT_FOUND (126) and a name that module does not export answered 0x0 with ERROR_PROC_NOT_FOUND (127); WriteFile returned 1 and wrote 33 byte(s); ... refusals 5 (the program's closed-handle pair, its refused write-access open, its absent-module and absent-export probes) -- OK, a real Windows-format console program started the way a CRT does -- its command line and its own image base, both read from loader state -- then resolved kernel32.dll by name, bound GetTickCount through GetProcAddress out of that module's own export directory and called twelve kernel32 exports through a real PE32+ DLL's stubs, which the kernel performed in ring 0 against a read-only in-memory volume, explaining every failure through GetLastError -- a real Win32 API surface, not Windows compatibility (no NT syscall layer, no directories or writes, ExitProcess records and returns)
```

The gate pins control 10, the `bindings=true` field, the runtime-binding
clause of the self-test sentence and both of its error codes, alongside
the moved `resolved 12 import(s)` needle. The client's own run raises
exactly five refusals -- the closed-handle pair, the refused
`GENERIC_WRITE` open, and this tranche's two deliberate negative probes,
which are visible in the transcript as `service refusal ModuleNotFound`
and `service refusal ProcNotFound` -- so every refusal the surface counts
is one the program asked for.

**Limits.** The table holds two rows (this process's image and the one
provider) and a third registration is a typed refusal; there is no
`LoadLibrary`/`FreeLibrary`, so a module is never unloaded and a row's
base and table can never go stale. `GetProcAddress` serves the name form
only (the ordinal form is refused by name), does not chase forwarded
exports (a forwarder is `ERROR_PROC_NOT_FOUND`), and matches a BARE
module name -- a path-qualified name is not matched against a basename,
because nothing here searches. The startup pair still carries no
arguments and no environment block (`GetEnvironmentStrings`,
`GetCommandLineW`, `GetStartupInfo`, `__getmainargs` are absent), there
is still no TEB, so the last error remains one cell per kernel rather
than per thread. Every v0/v0.1/v0.2/v0.3/v0.4 limit stands unchanged.

## The Win32 API surface v0.6: a second provider, and rows that are per-module (2026-11-22)

**The question this tranche answers.** v0.5 gave the loader a module table, but
every row it could hold was either this process's own image or `kernel32.dll` --
the one provider the client imports from. A table with a single provider cannot
be distinguished from a special case: a lookup that ignored the handle entirely
would still answer every question correctly, because every answer would come out
of the same export directory. So this tranche adds a SECOND provider and makes
the walk observable: `userprog/nyrqis.dll` exports two ordinary ring-3 functions
(`NyrqisBuild`, returning 6, and `NyrqisMode`, returning 1) that `kernel32.dll`
does not export, and the boot asks each provider for the other's name. A name
must resolve in exactly one row and answer `ERROR_PROC_NOT_FOUND` in the other,
from ring 3 as well as from the kernel-side controls.

**Why the second provider's exports are not trap stubs.** A `kernel32.dll` stub
exists to enter this kernel (`int $0x90`), and the service gate accepts a trap
only from `kernel32.dll`'s `.text`. An export served by the module table has no
such restriction -- it is just code in a mapped image -- so the second provider
is built as real function bodies that run entirely in ring 3. That also makes
the runtime binding a cleaner proof than the first provider could give: the
client has NO import descriptor for `nyrqis.dll`, so there is no IAT slot and no
link-time address, and the only route to those functions is the pointer
`GetProcAddress` returned. The client calls through it and reads 6 and 1.

**What changed.** `MAX_MODULES` is now 3 (this image + two providers), and a
fourth registration is still a typed refusal (`ModuleTableFull`, 14) rather than
a truncation. `win32::SECOND_IMAGE_BASE` pins the new image at
`0x190100000000`; the client's `GetModuleHandleA("nyrqis.dll")` must answer that
base and must NOT answer kernel32's. The client gained a tranche 6 and nine
report fields above `0x700` (past the `0x300` area the kernel controls write
their test paths into and past the export-name strings at `0x500`), and
`EXPECTED_REFUSALS` rose from 5 to 7: the absent export probe plus the two
cross-module probes, each of which is a question a Windows client is entitled to
an answer for.

**Pins moved, deliberately, and the build refuses the drift first.** The new
fixture is pinned in `build.sh` (`nyrqis.dll` `.text` `0000190100001000
00000030`) and in the demo (`EXPECTED_NYRQIS_EXPORTS`: `0x1000` and `0x1006`,
both inside that window). `kernel32.dll` is untouched: its `.text` stays
`0x1b0`, its call block `.data 0x40`, and the client's `SizeOfImage 0x4000` and
`.idata 0x1fc` (twelve imports) are unchanged -- the second provider is loaded
and registered by the loader, not linked into the client. The client's `.text`
grew from `0x5c8` to `0x6a8`, which no pin covers.

**Proved from both sides in the same boot.** Kernel-side, control 10 now
registers kernel32, nyrqis and the client as three rows, resolves all twelve
kernel32 exports and both nyrqis exports BY NAME out of their OWN export
directories, asks each provider for the other's name (`ProcNotFound` both ways),
and still refuses a duplicate, a fourth row, the wrong-case export name, an
unregistered base, the ordinal form and a NULL name. From ring 3 the client does
the same with its own strings and its own handles, calls through both runtime
pointers, and leaves the two refusals the proof is about visible on the serial
line.

```
experimental win32-demo: second provider nyrqis.dll loaded at its own ImageBase 0x190100000000; export table declares "nyrqis.dll" with 2 name(s) (NyrqisBuild, NyrqisMode), all inside .text (+0x1000..+0x1030) -- OK, a second image with its own export directory for the module table to serve
experimental win32-demo: negative control 10 -- with no module registered, GetProcAddress was refused as LoaderStateMissing and a named GetModuleHandleA as ModuleNotFound; five unusable rows (empty name, non-text name, zero base, zero image, an image too small for the exports it names) were refused as ModuleInstallInvalid and published nothing; kernel32.dll, nyrqis.dll and win32_console.exe then registered as three rows, both spellings of kernel32.dll's name answered 0x190000000000 and nyrqis.dll answered its own 0x190100000000, a duplicate and a fourth row were refused (ModuleInstallInvalid, ModuleTableFull at 3 rows), all 12 kernel32 exports and both nyrqis exports resolved BY NAME from their OWN export directories (12+2), a lowercase export name and the client's exportless image answered ProcNotFound, kernel32.dll asked for NyrqisBuild answered ProcNotFound and nyrqis.dll asked for GetTickCount answered ProcNotFound (so the rows are per-module), an unregistered base ModuleNotLoaded, an ordinal request OrdinalNotSupported and a NULL name NameInvalid -- OK, GetProcAddress answers PER MODULE from a module table the loader built and then certified
experimental win32-demo self-test checks: console=true hello=true bytes=true empty=true closed=true miss=true errors=true monotonic=true exit=true handles=true refusals=true handle_test=1 startup=true bindings=true modules=true; handles f1=0x100 ... 
experimental win32-demo self-test: ... a second provider, nyrqis.dll, answered its own row's base 0x190100000000 (different from kernel32's), and its NyrqisBuild and NyrqisMode exports resolved there at 0x190100001000 and 0x190100001006, which the client CALLED through, reading 6 and 1; kernel32.dll asked for NyrqisBuild answered 0x0 with ERROR_PROC_NOT_FOUND (127) and nyrqis.dll asked for GetTickCount answered 0x0 the same (127); ... refusals 7 (the program's closed-handle pair, its refused write-access open, its absent-module and absent-export probes, and its two cross-module probes) -- OK, a real Windows-format console program started the way a CRT does ...
```

The gate pins the new provider's load line, the three-row registration, the
per-module clause of control 10, `modules=true`, the two runtime-bound nyrqis
addresses with the values 6 and 1 they returned, and both cross-module
`ERROR_PROC_NOT_FOUND (127)` answers. The client's own run raises exactly seven
refusals, and the transcript shows them: two `File(ClosedHandle)`, one
`UnsupportedAccess`, one `ModuleNotFound`, and three `ProcNotFound` -- the
absent-export probe plus the two directions of the per-module proof.

**Limits.** The table now holds three rows (this image and two providers) and a
fourth registration is a typed refusal; there is still no
`LoadLibrary`/`FreeLibrary`, so a module is never unloaded and a row's base and
table can never go stale. `GetProcAddress` still serves the name form only, does
not chase forwarded exports, and matches a BARE module name. The second
provider is a fixture, not a second API surface: its two functions exist to make
the module table's per-module claim testable, and no kernel operation is
dispatched on their behalf. The startup pair, the error table, the TEB limit and
every v0/v0.1/v0.2/v0.3/v0.4/v0.5 limit stand unchanged.
## The Win32 API surface v0.7: GetProcAddress's ordinal form, out of the file's own OrdinalBase (2026-11-22)

**The question this tranche answers.** v0.6's `GetProcAddress` served names only: a
low-word argument -- Windows' documented ordinal form, `(LPCSTR)(ULONG_PTR)n` --
was refused with `ERROR_NOT_SUPPORTED` because the module table had never read
an ordinal. But the ordinal is not a second table's question. `IMAGE_EXPORT_DIRECTORY`
declares `OrdinalBase`, an ordinal names EAT slot `n - OrdinalBase`, and the
table the named form already walks is the same EAT the ordinal indexes. So the
refusal was a gap in the surface, not a missing file fact, and this tranche closes
it: `pe::DllExportTable::lookup_ordinal` resolves the form from the provider's
own directory, and `Win32Error::OrdinalNotSupported` is gone from the enum and
from the 28-row error-code table (which is now 27 rows).

**Why the second provider now declares OrdinalBase 10.** With the linker's
default base of 1, "ordinal N" and "EAT index N-1" are the same number for every
export, so a boot that asked ordinal 2 would pass whether or not the
implementation read the base at all -- the proof would be decorative. So
`userprog/nyrqis.def` states `NyrqisBuild @10` and `NyrqisMode @11`, making the
provider's exports ordinals 10 and 11. `build.sh` pins the linked DLL's own
`Export Address Table -- Ordinal Base 10`, so a DEF file this ld stopped honoring
fails the build before a boot can weaken. The two export RVAs and `.text` are
unchanged (`0x1000`, `0x1006`, `0x30`) -- only the numbering the directory
declares is different.

**What changed.** `DllExportTable` gained `lookup_ordinal` (absent for ordinal 0,
below the base, past `OrdinalBase + NumberOfFunctions`, a null EAT slot, or a
forwarder -- never a guessed slot) and an `ordinal_base()` accessor. Arm 11 of
`dispatch` now decides on the VALUE: below `ORDINAL_LIMIT` it resolves through
the selected row's own base, at or above it it reads a name pointer as before.
The client gained a tranche 7 with seven report fields at `0x748..0x778`: the
ordinal answer for 11, whether it equals the NAME answer, the value read by
CALLING through it, and the two out-of-range probes. `EXPECTED_REFUSALS` rose
from 7 to 9 -- ordinals 9 and 12, from ring 3 -- while the kernel-side control
grew the same probes from the other side: ordinals 10 and 11 equal their named
answers, kernel32's slot 0 (ordinal 1 under base 1) is CloseHandle, and 9 and 12
answer `ProcNotFound`.

**Pins moved, deliberately, and the build refuses the drift first.** `nyrqis.def`
is new and pinned through `objdump -p` (`Ordinal Base 10`); `nyrqis.dll`'s
`.text` stays `0000190100001000 00000030`, and its `.edata` is still `0x5e`.
`kernel32.dll` is untouched. The client's `.text` grew from `0x6a8` to `0x758`;
its `.idata` stays `0x1fc` (twelve imports) and `SizeOfImage` stays `0x4000`.

**Proved from both sides in the same boot.** Kernel-side, control 10 now states
the ordinal facts beside the named ones; from ring 3 the client asks the second
provider for ordinal 11, compares that address with the one the NAME answered,
CALLS through it and reads 1, then asks for 9 and 12 and gets NULL with
`ERROR_PROC_NOT_FOUND` both times.

```
experimental win32-demo: negative control 10 -- with no module registered, GetProcAddress was refused as LoaderStateMissing and a named GetModuleHandleA as ModuleNotFound; five unusable rows (empty name, non-text name, zero base, zero image, an image too small for the exports it names) were refused as ModuleInstallInvalid and published nothing; kernel32.dll, nyrqis.dll and win32_console.exe then registered as three rows, both spellings of kernel32.dll's name answered 0x190000000000 and nyrqis.dll answered its own 0x190100000000, a duplicate and a fourth row were refused (ModuleInstallInvalid, ModuleTableFull at 3 rows), all 12 kernel32 exports and both nyrqis exports resolved BY NAME from their OWN export directories (12+2), a lowercase export name and the client's exportless image answered ProcNotFound, kernel32.dll asked for NyrqisBuild answered ProcNotFound and nyrqis.dll asked for GetTickCount answered ProcNotFound (so the rows are per-module), an unregistered base ModuleNotLoaded, a NULL name NameInvalid, and the ORDINAL form answered from the same directories -- nyrqis.dll's declared OrdinalBase 10 made its first two exports ordinals 10 and 11 (both answered the addresses their NAMES did), kernel32.dll's first slot answered ordinal 1 under the ordinary base 1, and ordinals 9 and 12 (below that base and past its last slot) answered ProcNotFound rather than an index guess -- OK, GetProcAddress answers PER MODULE from a module table the loader built and then certified
experimental win32-demo self-test checks: console=true hello=true bytes=true empty=true closed=true miss=true errors=true monotonic=true exit=true handles=true refusals=true handle_test=1 startup=true bindings=true modules=true ordinals=true; handles f1=0x100 f2=0x100 f3=0x100 f4=0x100 f5=0xffffffffffffffff f6=0xffffffffffffffff; closes 1 1 1 1; reads 1/1/1/1/1/0
experimental win32-demo self-test: ... kernel32.dll asked for NyrqisBuild answered 0x0 with ERROR_PROC_NOT_FOUND (127) and nyrqis.dll asked for GetTickCount answered 0x0 the same (127); asked in Windows' ordinal form, the second provider's base-11 ordinal 11 answered 0x190100001006 -- the same address its NAME did (1) -- which the client CALLED through, reading 1; ordinals 9 and 12 (below the base, past the last slot) answered 0x0 and 0x0 with ERROR_PROC_NOT_FOUND (127) and (127); ... refusals 9 (the program's closed-handle pair, its refused write-access open, its absent-module and absent-export probes, and its two cross-module probes, and its two out-of-range ordinal probes) -- OK, a real Windows-format console program ...
```

The gate pins the ordinal clause of control 10 (`OrdinalBase 10 ... ordinals 10
and 11`, the 9/12 refusals), `ordinals=true` on the checks line, and the ring-3
self-test sentence -- the ordinal-11 address `0x190100001006`, equal-to-name
`1`, and both `ERROR_PROC_NOT_FOUND (127)` answers. The client's run raises
exactly nine refusals: two `File(ClosedHandle)`, one `UnsupportedAccess`, one
`ModuleNotFound`, and five `ProcNotFound` -- the absent-export probe, the two
cross-module probes, and the two out-of-range ordinals.

**Limits.** The ordinal form is answered per module from the same three-row table,
so it inherits every v0.5/v0.6 limit: no `LoadLibrary`/`FreeLibrary`, no
forwarder chase (a forwarded ordinal answers `ERROR_PROC_NOT_FOUND`, exactly as a
forwarded name does -- the import walk still chases them), and bare module names.
It adds no ordinal cache and no second name list: the answer is still the mapped
file's EAT. The startup pair, the error table, the TEB limit and every
v0/v0.1/v0.2/v0.3/v0.4/v0.5/v0.6 limit stand unchanged.

## The Win32 API surface v0.8: GetProcAddress chases forwarded exports (2026-11-22)

**The question this tranche answers.** v0.7 closed the gap between the name table
and the EAT, but both forms still stopped at an EAT slot whose value lies INSIDE
the export directory. That is a forwarded-export STRING (`other.dll.other_fn`),
and Windows' `GetProcAddress` follows it into the named module's own export
directory rather than failing. The module table already IS the list of loaded
modules, so answering `ERROR_PROC_NOT_FOUND` for such a slot was a missing
chase, not a fact about the file. This tranche adds it: a forwarder's answer is
the TARGET module's real address, and only a chain that cannot land stays
`ERROR_PROC_NOT_FOUND`.

**Why the fixture needs a real forwarder.** A proof cannot be built from a
string the demo invents: the linker must write the string into the EAT. So
`userprog/nyrqis.def` now declares two forwarded exports beside its two
functions: `NyrqisTick = kernel32.dll.GetTickCount @12` and
`NyrqisGhost = kernel32.dll.NoSuchExport @13`. The first names a real export of
the OTHER provider, so its answer must be kernel32's own code -- an address
outside `nyrqis.dll`; the second names an export kernel32 does not have, so the
same chase must end in `ERROR_PROC_NOT_FOUND`. With four contiguous EAT slots
(ordinals 10..13), the "past the last slot" probe moves from 12 to 14, and 12
becomes the forwarder's own ordinal: the boot asks it as a POSITIVE probe, which
proves the ordinal form chases through the same table.

**What changed.** `pe::DllExportTable::resolve_export` classifies one EAT slot as
`ExportAnswer::Address` or `ExportAnswer::Forward { dll, symbol }`; `lookup` and
`lookup_ordinal` are now built on it, so the name form and the ordinal form
share ONE classification instead of each re-deriving "is this RVA inside the
directory". `pe::DllRegistry::chase` uses the same method, so the import walk
and the module table cannot disagree about what a forwarder is.
`win32::resolve_proc` performs the row-to-row walk: it matches the forwarder's
module with the same case-insensitive rule every module question uses, retries
`<name>.dll` when the string omits the extension (forwarder strings
conventionally do), caps the chain at `FORWARDER_DEPTH_LIMIT` (8, the import
walk's own cap), and maps every failure -- absent target module, absent target
export, malformed string, a chain that only forwards -- to `ERROR_PROC_NOT_FOUND`,
Windows' single answer for an export that cannot be produced. The three-row
table, its install-time certification, and every error code are unchanged (no
new `Win32Error` variant, still a 27-row code table).

**A collision the first boot caught.** The kernel-side forwarder probes write
their names into the client report page. The first placement put `NyrqisTick` at
`0x660` -- exactly the twelfth export-name slot the twelve-kernel32-name loop
writes -- so the loop overwrote it with `WriteFile` before the probe and the
surface correctly refused a name `nyrqis.dll` does not export. The boot failed
on the new assertion rather than passing for the wrong reason. The forwarder
names now sit at `0x680`/`0x6A0`, above the loop and below the client's fields
at `0x700`, with the reason recorded in the fixture.

**Pins moved, deliberately, and the build refuses the drift first.** `build.sh`
now pins both forwarder strings out of `objdump -p`
(`kernel32.dll.GetTickCount` and `kernel32.dll.NoSuchExport`) beside the existing
`Ordinal Base 10` pin, so a linker that stopped writing forwarders fails the
build before a boot can weaken. `nyrqis.dll`'s `.text` stays
`0000190100001000 00000030`; its `.edata` grows from `0x5e` to `0xbd` (the two
strings). `kernel32.dll` is untouched. The client's `.text` grows from `0x758`
to `0x7e8`; its `.idata` stays `0x1fc` (twelve imports) and `SizeOfImage` stays
`0x4000`.

**Proved from both sides in the same boot.** Kernel-side, the second-provider
pin asserts that each forwarder's `lookup` answers nothing out of its OWN image
and that `resolve_export` names exactly `kernel32.dll` +
`GetTickCount`/`NoSuchExport`; the surface then answers `NyrqisTick` with
kernel32's own `GetTickCount` address, BY NAME and BY ORDINAL 12, and refuses
`NyrqisGhost` with `ProcNotFound`. From ring 3 the client asks for `NyrqisTick`,
compares the answer with the address kernel32's row gave `GetTickCount`, CALLS
through it and reads a live tick, then asks for `NyrqisGhost` and gets NULL with
`ERROR_PROC_NOT_FOUND`.

```
experimental win32-demo: second provider nyrqis.dll loaded at its own ImageBase 0x190100000000; export table declares "nyrqis.dll" with 4 name(s) (two functions, two forwarded exports), the functions inside .text (+0x1000..+0x1030) -- OK, a second image whose EAT the module table serves, forwarders included
experimental win32-demo: forwarded exports -- nyrqis.dll's NyrqisTick (a forwarder to kernel32.dll.GetTickCount) answered 0x19000000104d, the very address kernel32.dll's own row gives GetTickCount, BY NAME and BY ORDINAL 12; NyrqisGhost (a forwarder to kernel32.dll.NoSuchExport) answered ProcNotFound with ERROR_PROC_NOT_FOUND (127) because the target module has no such export -- OK, a forwarder is chased through the module table into the target row's own export directory, not answered out of the forwarder's image
experimental win32-demo self-test checks: console=true hello=true bytes=true empty=true closed=true miss=true errors=true monotonic=true exit=true handles=true refusals=true handle_test=1 startup=true bindings=true modules=true ordinals=true forwarders=true; handles f1=0x100 f2=0x100 f3=0x100 f4=0x100 f5=0xffffffffffffffff f6=0xffffffffffffffff; closes 1 1 1 1; reads 1/1/1/1/1/0
experimental win32-demo self-test: ... ordinals 9 and 14 (below the base, past the last slot) answered 0x0 and 0x0 with ERROR_PROC_NOT_FOUND (127) and (127); its forwarded export NyrqisTick (kernel32.dll.GetTickCount) answered 0x19000000104d BY NAME and BY ORDINAL 12 -- the same address kernel32.dll's own row gave GetTickCount (1) -- which the client CALLED through, reading 230340; a forwarder to an export kernel32.dll does not have answered 0x0 with ERROR_PROC_NOT_FOUND (127); ... refusals 10 (the program's closed-handle pair, its refused write-access open, its absent-module and absent-export probes, its two cross-module probes, its two out-of-range ordinal probes, and its forwarder-to-an-absent-export probe) -- OK, a real Windows-format console program ...
```

The gate pins the `4 name(s) ... forwarders included` load line, the full
forwarder line above, `forwarders=true` on the checks line, the ring-3
`NyrqisTick ... BY NAME and BY ORDINAL 12` clause and the ghost's
`ERROR_PROC_NOT_FOUND (127)`, and the updated 9/14 ordinal clause. The client's
run raises exactly ten refusals: two `File(ClosedHandle)`, one
`UnsupportedAccess`, one `ModuleNotFound`, and six `ProcNotFound` -- the
absent-export probe, the two cross-module probes, the two out-of-range ordinal
probes, and the forwarder whose target export is absent.

**Limits.** The chase is bounded by the same three-row table as v0.5-v0.7: no
`LoadLibrary`/`FreeLibrary`, so a forwarder can only land in a module the loader
already registered; bare module names for the handle argument; eight forwarder
hops; and Windows' one failure code for every way the chase can fail. A
forwarder string is read and split by the provider's own directory, but it is
not validated at install time -- `GetProcAddress` reports a malformed one as
`ERROR_PROC_NOT_FOUND`, while the import walk keeps its typed
`MalformedForwarder`. The startup pair, the error table, the TEB limit and every
v0/v0.1/v0.2/v0.3/v0.4/v0.5/v0.6/v0.7 limit stand unchanged.


## The Win32 API surface v0.9: LoadLibraryA/FreeLibrary -- the module table grows at runtime (2026-11-23)

Every module-table row before this increment was installed by this kernel's own
loader before ring 3 started, so a table that could never change answered every
question exactly like a table that could: the property "the module table is a
LOADER's table" was untestable. This increment adds kernel32's 13th and 14th
exports, `LoadLibraryA` and `FreeLibrary`, and with them the first rows a
program creates and destroys itself.

`LoadLibraryA(path)` reads the FILE the path names out of the read-only volume
(`win32_fs::file_bytes` -- the same path rules `CreateFileA` applies), hands its
bytes to a loader hook installed beside the surface (`win32::install_dynamic_loader`),
and only then adds a row. The hook lives with the address space and frame
allocator (`win32_demo::load_dynamic_module`) because those are the caller's,
exactly where Windows draws the line between kernel32!LoadLibraryA and the
loader behind it; it maps the image at its own `ImageBase`, refuses to return
any other base, and parses the export table out of the MAPPED bytes -- the same
discipline every other row follows. A name already in the table answers THAT
row's base and increments a reference count (`Module::refs`), because `HMODULE`
identity is per image and two rows for one file would make `FreeLibrary`'s
count meaningless. `FreeLibrary` decrements, and at zero removes the row
(`remove_module_at`, keeping rows a prefix with no holes). A base that is not a
live row is `ERROR_INVALID_HANDLE`.

The proof's target is a forwarder that only a runtime row can satisfy. The new
`nyrqis_dyn.dll` is a real PE32+ file that lives on the volume as file content
(`win32_fs::FILES[3]`, `include_bytes!`), never as a kernel symbol, and
`nyrqis.def` gained a third forwarded export, `NyrqisDyn = nyrqis_dyn.dll.NyrqisDynValue @14`.
So one `GetProcAddress(nyrqis.dll, "NyrqisDyn")` question has three answers,
decided only by what is loaded: `ERROR_PROC_NOT_FOUND` before the load, the
dynamic image's real export at `+0x1000` while the row exists (which the client
calls, reading 42 from code it never linked against), and
`ERROR_PROC_NOT_FOUND` again after `FreeLibrary` dropped the row.

`MAX_MODULES` is now four -- the process image, the two link-time providers,
and ONE runtime module -- and the cap is proved REACHABLE: the fourth row is
live when a fifth registration is asked for, and it is `ERROR_OUTOFMEMORY`
(`ModuleTableFull`). A DLL the volume does not have answers NULL with
`ERROR_MOD_NOT_FOUND`, a failure VALUE rather than a refusal, because a program
probing for an optional DLL is entitled to a value it can test; a file that is
present and not a loadable image is the typed refusal `ImageLoadFailed`
(`ERROR_BAD_EXE_FORMAT`, 193), the 28th row of the error table. `LoadLibraryA`
and `FreeLibrary` answer 0 when refused, the documented failure of `HMODULE`
(NULL) and `BOOL`.

**What the unload does NOT do**, stated rather than implied: this kernel's PE
loader has no unmap, so `FreeLibrary` removes the ROW and leaves the image's
pages mapped. A later `LoadLibraryA` of the same file therefore re-registers the
image at the base it already occupies instead of failing on a present page --
the row is what comes and goes. That is a limit of the mapping layer, not a
claim about a Windows unload.

**Two bugs the first boots caught.** (1) A stack budget: with fourteen imports
in the client's `.idata` and this stage's added lifecycle block, the stage's
eight-page RSP0 stack ran out at the REAL import resolution -- one frame later
than the new code. QEMU's `-d int` log named the first fault precisely: a
supervisor WRITE to `CR2=0x140000affe0`, the guard page below the region, whose
`#PF` the fault handler turned into the printed `#DF`. `WIN32_STACK_PAGES`'s budget
is now twelve pages, stated with the measurement rather than grown one page at
a time. (2) The same string-slot collision as v0.8, one slot later: the
export-name loop now writes FOURTEEN names at `0x500 + i*0x20`, whose 13th slot
is `0x680` -- where this tranche's first draft had put `"NyrqisTick"`. The
surface then correctly refused a name `nyrqis.dll` does not export, and the
forwarder names moved to `0x6C0`/`0x6E0`, above the loop and below the client's
fields.

**Last verified boot transcript** (`tools/qemu_boot_test.sh`, passing):

```
  experimental win32-demo: kernel32.dll loaded at its own ImageBase 0x190000000000; export table declares "kernel32.dll" with 14 name(s), all inside .text (+0x1000..+0x11e8); call block magic 0x4e59525733325f31 at +0x2000 -- OK
  experimental win32-demo: resolved 14 import(s) from the client's REAL .idata against kernel32.dll's REAL export directory (GetStdHandle, WriteFile, GetTickCount, ExitProcess, CreateFileA, ReadFile, CloseHandle, GetLastError, SetLastError, GetCommandLineA, GetModuleHandleA, GetProcAddress, LoadLibraryA, FreeLibrary), every slot reading back its export address -- OK
  experimental win32-demo: negative control 7 -- ... a successful open left the code alone, and SetLastError(0x1234) read back through GetLastError -- OK
experimental win32-demo: runtime modules -- nyrqis.dll's forwarder NyrqisDyn answered ProcNotFound with no nyrqis_dyn.dll row; LoadLibraryA("C:\nyrqis\nyrqis_dyn.dll") answered the image's own base 0x190200000000 and made it a fourth row, a SECOND load of the same file answered the same base with no new row, a fifth registration was refused as ModuleTableFull (4 rows), the module's name and the forwarder both answered 0x190200001000 (its own export, at the RVA its file states, and the client's own row answered the same), one FreeLibrary left it loaded with the forwarder still resolving, the second dropped the row so the name answered ERROR_MOD_NOT_FOUND (126) and the forwarder ERROR_PROC_NOT_FOUND (127), and FreeLibrary of the released base was refused as ModuleNotLoaded; a DLL the volume does not have answered NULL with ERROR_MOD_NOT_FOUND (126) -- OK, the module table grows and shrinks while a program runs
; BEFORE LoadLibraryA, nyrqis.dll's forwarder NyrqisDyn (naming nyrqis_dyn.dll.NyrqisDynValue) answered 0x0 with ERROR_PROC_NOT_FOUND (127), LoadLibraryA("C:\nyrqis\nyrqis_dyn.dll") then answered the runtime image's own base 0x190200000000, the file name GetModuleHandleA("nyrqis_dyn.dll") answered the same base 0x190200000000 (a row for a module the client never linked against), the SAME forwarder then answered 0x190200001000 -- the dynamic row's own export at +0x1000 -- which the client CALLED through, reading 42, FreeLibrary answered 1 and the forwarder went back to 0x0 with ERROR_PROC_NOT_FOUND (127), and a DLL the volume does not have answered 0x0 with ERROR_MOD_NOT_FOUND (126)
-- OK, a real Windows-format console program started the way a CRT does -- its command line and its own image base, both read from loader state -- then resolved kernel32.dll and a second provider, nyrqis.dll, by name AND by Windows' ordinal form (the second provider's declared OrdinalBase 10 making its exports ordinals 10 and 11, never bare indices), bound GetTickCount through GetProcAddress out of each module's own export directory (every name resolving only in the row that exports it), chased a forwarded export into the module it names (nyrqis.dll's NyrqisTick answering kernel32.dll's real GetTickCount, by name and by ordinal), then LOADED A MODULE AT RUNTIME -- LoadLibraryA mapping nyrqis_dyn.dll out of the read-only volume into a new row, the same forwarder resolving into it (NyrqisDyn answering the dynamic image's own export, which the client called for 42), and FreeLibrary dropping that row so the name and the forwarder failed again -- and called fourteen kernel32 exports plus two nyrqis exports through real PE32+ DLLs, which the kernel performed in ring 0 for the kernel32 half against a read-only in-memory volume, explaining every failure through GetLastError -- a real Win32 API surface, not Windows compatibility (no NT syscall layer, no directories or writes, ExitProcess records and returns)
experimental win32-demo self-test checks: console=true hello=true bytes=true empty=true closed=true miss=true errors=true monotonic=true exit=true handles=true refusals=true handle_test=1 startup=true bindings=true modules=true ordinals=true forwarders=true dynamic=true; handles f1=0x100 f2=0x100 f3=0x100 f4=0x100 f5=0xffffffffffffffff f6=0xffffffffffffffff; closes 1 1 1 1; reads 1/1/1/1/1/0
```

The gate pins the `14 name(s)` and `resolved 14 import(s)` load lines, the v0.8
forwarder lines unchanged, the full `runtime modules` line above (the
`ProcNotFound` before the load, the `0x190200000000` base and fourth row, the
same base for a second load with no new row, `ModuleTableFull (4 rows)`, the
freed row's `126`/`127` pair, the `NULL` + `126` missing-DLL answer), the
client's own clauses for the same sequence, `dynamic=true` on the checks line,
the 9/15 ordinal clause, and `refusals 12`. The client's run raises exactly
twelve refusals: two `File(ClosedHandle)`, one `UnsupportedAccess`, one
`ModuleNotFound`, and EIGHT `ProcNotFound` -- the absent-export probe, the two
cross-module probes, the two out-of-range ordinal probes, the
forwarder-to-an-absent-export probe, and the two `NyrqisDyn` probes that bracket
the runtime load.

**Measured.** Client `.text` `0x7e8 -> 0x908`, `.idata` `0x1fc -> 0x244`,
`SizeOfImage 0x4000` (unchanged); `kernel32.dll` `.text` `0x1b0 -> 0x1e8` (the
two stubs appended at the end, so all twelve earlier RVAs are unchanged --
`GetProcAddress` is still `0x1168`); `nyrqis.dll` `.edata 0xbd` with three
forwarder strings; new `nyrqis_dyn.dll` `.text` `0x1000..0x1028`, `ImageBase
0x190200000000`, `SizeOfImage 0x4000`, one export `NyrqisDynValue @1`.

**Limits.** One runtime slot (a second dynamic DLL is `ERROR_OUTOFMEMORY`); no
unmap on `FreeLibrary` (the row goes, the pages stay); `LoadLibraryA` takes a
fully qualified volume path rather than a bare name searched along a load path,
and does not resolve the loaded image's own imports (the dynamic DLL has none);
the loader hook is kernel-installed state, not a service ring 3 can point
somewhere else. The v0.8 limit list stands otherwise: bare module names for the
handle argument, eight forwarder hops, one code for every way a chase can fail,
no forwarder-string validation at install time, one last-error cell and no TEB.

## The Win32 API surface v1.0: GetModuleFileNameA -- a handle says which FILE it came from (2026-11-23)

A module handle was still an address with no history: `GetProcAddress` used it
to pick a row, `FreeLibrary` to drop one, and neither could tell a program
which file the code behind it came from. kernel32's 15th export fixes that, and
the fix changed the SHAPE of a row rather than adding a lookup on top of it.

`install_module` now takes the PATH and derives the module NAME from it
([`module_name_from_path`]) instead of the other way round -- the direction
Windows' loader works in -- so a row literally cannot answer "where did I come
from" and "what am I called" with two strings that name different files. The
row stores the path through the volume's own bound (`MAX_MODULE_PATH` = 260
bytes, `path_len: u16`), and `GetModuleFileNameA` answers out of that record:
`NULL` is the executable (this process's own row), any other handle must be a
LIVE row (`ERROR_INVALID_HANDLE` otherwise, the same evidence rule
`FreeLibrary` applies), and the copy is Windows' truncation contract --
`nSize` counts the NUL, a path that does not fit is cut to `nSize - 1` bytes
and still terminated, and the return is `nSize` with
`ERROR_INSUFFICIENT_BUFFER`; `nSize == 0` writes nothing and answers 0 with the
same code. The destination is validated as a WRITE over its whole `nSize`
range, so the kernel can never be made to write across a mapping edge.

The interesting row is the one `LoadLibraryA` created. Its path is the string
the client handed the loader at runtime -- no byte of it exists in the client's
image, and nothing in the kernel's own data but that row -- so the boot asks
for it while the row is live, twice: once from the kernel, comparing against
the recorded path byte for byte, and once from ring 3, where the client
compares the answer against its own copy.

**Five things the first boots caught**, each with the fix stated:

1. Index 14 had been the "unknown function" negative control; it is now an
   export. Control 3 moved to index 15 (one past the last export) and its
   transcript line says so.
2. Fifteen export names made the name loop write through `0x6C0..0x6E0`, which
   was where the v0.8 forwarder names sat. They moved to `0x4E0`/`0x4F0` (the
   gap above the second provider's own names, below the loop), with the reason
   in the comment -- this block has now moved three times for one cause.
3. The kernel-side scratch buffer for the new control first sat at `0x900`,
   which is where the runtime module's own path string lives; it moved to the
   free `0x6E0` gap (32 bytes: enough for the 27-byte client path plus NUL).
4. `LoadLibraryA` was registering its row under the module NAME it had just
   derived, not the path -- so the runtime row answered 14 bytes instead of 24
   and the kernel-side assertion named it. Arm 12 now passes `path`, which is
   also the more honest thing to store.
5. The client's truncation prefix was written as 8 bytes where an 8-byte buffer
   holds 7, and the NUL check compared a one-byte slice against `b""` (a
   zero-length slice). Both are the kind of off-by-one a boot finds and a
   reading does not.

**Last verified boot transcript** (`tools/qemu_boot_test.sh`, passing):

```
  experimental win32-demo: module files -- GetModuleFileNameA(NULL) answered "C:\nyrqis\win32_console.exe" (27 bytes) for this process's own row, kernel32.dll's handle answered "C:\nyrqis\kernel32.dll" and nyrqis.dll's "C:\nyrqis\nyrqis.dll" -- the paths the loader installed them under, each compared byte for byte; the RUNTIME row answered the path LoadLibraryA was given while it was loaded, an 8-byte buffer cut that 27-byte path to 7 bytes plus NUL and answered 8 with ERROR_INSUFFICIENT_BUFFER (122), nSize 0 wrote nothing and answered 0 with the same code, and the freed row's base was refused as ModuleNotLoaded -- OK, a module handle answers which file it came from
  experimental win32-demo self-test checks: console=true hello=true bytes=true empty=true closed=true miss=true errors=true monotonic=true exit=true handles=true refusals=true handle_test=1 startup=true bindings=true modules=true ordinals=true forwarders=true dynamic=true files=true; handles f1=0x100 f2=0x100 f3=0x100 f4=0x100 f ...
resolved 15 import(s) from the client's REAL .idata against kernel32.dll's REAL export directory (GetStdHandle, WriteFile, GetTickCount, ExitProcess, CreateFileA, ReadFile, CloseHandle, GetLastError, SetLastError, GetCommandLineA, GetModuleHandleA, GetProcAddress, LoadLibraryA, FreeLibrary, GetModuleFileNameA), every sl ...
```

The gate pins the new `module files` line in three pieces (the three startup
paths, the runtime row's provenance and the truncation pair, and the freed
base's refusal), the client's own clause for the same sequence
(`asked WHICH FILE each handle came from`), `files=true` on the checks line,
the 15-name and 15-import load lines, and the runtime-modules line's new
sentence about `GetModuleFileNameA` answering that row. Needle count 59, all
matched against a real boot.

**Measured.** `kernel32.dll` `.text` `0x1e8 -> 0x210` with the new stub appended
last (`GetModuleFileNameA` at RVA `0x11C3`, so every earlier RVA is unchanged);
client `.text` `0x7e8 -> 0xb48`, `.idata` `0x1fc -> 0x270`, `SizeOfImage
0x4000` and ImageBase unchanged; kernel32 declares 15 exports; the client
imports all 15. The client's run raises 13 refusals (the twelfth tranche's 12
plus its own `GetModuleFileNameA` probe on a base that names no row, which
answers 0 with `ERROR_INVALID_HANDLE`).

**Limits.** The SHORT path form only (no `\\?\` prefixes, no 8.3 aliases, no
case normalisation: the bytes are the loader's, returned verbatim); the three
provider images are embedded in this kernel rather than read from the volume,
so their recorded path is the load path this boot installed them under and
nothing re-checks it against `win32_fs::FILES`; there is no `GetModuleFileNameW`
and no long-path API; and the truncation terminates the copy, which is the
kinder half of the documented Windows behaviour (older Windows leaves a
truncated buffer unterminated) and is stated rather than assumed. Everything
the v0.9 limit list says about the table itself still holds: one runtime slot,
no unmap on `FreeLibrary`, bare names for the handle argument of
`GetModuleHandleA`, eight forwarder hops, one code per failure family, no
forwarder-string validation at install time, one last-error cell and no TEB.

## The Win32 API surface v1.1: the process heap -- the first memory a Windows program owns (2026-11-24)

Everything the surface had given a Windows console program until now was a
handle to something the kernel owned: a console, a file, a module, a last-error
cell. `HeapAlloc` is the first call whose result the PROGRAM owns -- a pointer
ring 3 writes through -- and that changes what the surface has to know. The
kernel's own heap is supervisor-only, so a block out of it would fault the
moment a Windows program stored to it. So the heap here is a client-writable
REGION: the boot proof maps two pages at `0x4D00_0010_0000`, `install_heap`
records the range, and `HeapAlloc` carves 16-byte-aligned blocks out of it. That
is the same split `LoadLibraryA` already uses -- the surface owns the
bookkeeping, the loader owns the address space.

Three exports, eighteen in all:

- `GetProcessHeap()` answers one stable handle (`0x48`). A call before a region
  is registered is the typed `HeapNotInstalled` (`ERROR_NOT_READY`), never an
  invented handle.
- `HeapAlloc(hHeap, dwFlags, dwBytes)` accepts `HEAP_ZERO_MEMORY` and
  `HEAP_NO_SERIALIZE`; `HEAP_GENERATE_EXCEPTIONS` is REFUSED because this
  kernel has no Windows exception dispatcher to raise into, and an unknown bit
  is a bad argument. A zero-byte request gets a distinct, freeable address
  (Windows documents that shape, and a CRT's `malloc(0)` can reach it), so one
  alignment unit is reserved for it. A freed block is offered to the next
  request that fits, which is what makes `HeapFree` a RETURN of memory rather
  than a flag; a reused block keeps its original size (no splitting -- stated,
  not hidden). A request larger than the region, a full block table, or an
  overflowing size is `HeapExhausted` (`ERROR_NOT_ENOUGH_MEMORY`).
- `HeapFree(hHeap, dwFlags, lpMem)` requires a LIVE block of this heap. A
  double free, an address never issued, or a value the client invented is
  `HeapBlockNotLive` (`ERROR_INVALID_ADDRESS`) instead of a corrupted table --
  Windows leaves this undefined, so refusing it is the honest half of the
  contract.

**The ring-3 proof** (a real PE32+ console client, through real trap stubs):
`GetProcessHeap` answered `0x48`; `HeapAlloc(h, 0, 64)` and
`HeapAlloc(h, HEAP_ZERO_MEMORY, 32)` answered `0x4d0000100020` and
`0x4d0000100000` -- 16-byte aligned, inside the region, disjoint, and the zeroed
block read back zero; the client WROTE `0x41` through its own allocation and
read it back (a kernel-heap-backed allocator would fault there); `HeapFree`
answered 1 and the second free 0 with `ERROR_INVALID_ADDRESS` (487); the next
16-byte allocation answered the freed address, so Free returned memory; a
foreign handle answered 0 with `ERROR_INVALID_HANDLE` (6),
`HEAP_GENERATE_EXCEPTIONS` 0 with `ERROR_INVALID_PARAMETER` (87), a 1 MiB
request 0 with `ERROR_NOT_ENOUGH_MEMORY` (8), and a free of an address never
issued 0 with 487 again. The kernel-side control (`3b`) checks the region,
alignment and zeroing before ring 3 runs; the refusal shapes are the client's
own probes.

**Last verified boot transcript** (a live BIOS ISO boot; the Win32 chain entered
directly for this run because of the unrelated `elf_dyn_demo` limit below):

```
  experimental win32-demo: kernel32.dll loaded at its own ImageBase 0x190000000000; export table declares "kernel32.dll" with 18 name(s), all inside .text (+0x1000..+0x1260); call block magic 0x4e59525733325f31 at +0x2000 -- OK
  experimental win32-demo: negative control 3b -- GetProcessHeap answered the surface's handle; a 32-byte HEAP_ZERO_MEMORY block landed 16-byte aligned inside the 8192 byte region and read back zero; HeapFree answered TRUE for it -- OK, the ring-3 client below exercises the refusal shapes
  experimental win32-demo: the process heap -- GetProcessHeap answered 0x48; HeapAlloc(0, 64) and HeapAlloc(HEAP_ZERO_MEMORY, 32) answered 0x4d0000100020 and 0x4d0000100000, both 16-byte aligned inside the region and disjoint (1), the zeroed block read back zero (1), and the client WRITING and reading a byte through its own allocation 1; HeapFree answered 1 then 0, with ERROR_INVALID_ADDRESS (487) on the double free; the next 16-byte allocation answered 0x4d0000100020 -- the freed address, so Free returned memory (1); a foreign handle answered 0x0 with ERROR_INVALID_HANDLE (6), HEAP_GENERATE_EXCEPTIONS answered 0x0 with ERROR_INVALID_PARAMETER (87), a 1 MiB request answered 0x0 with ERROR_NOT_ENOUGH_MEMORY (8), and a free of an address never issued answered 0x0 with ERROR_INVALID_ADDRESS (487) -- OK, a real Windows program can allocate and free its own memory through this kernel
  experimental win32-demo self-test checks: console=true hello=true bytes=true empty=true closed=true miss=true errors=true monotonic=true exit=true handles=true refusals=true handle_test=1 startup=true bindings=true modules=true ordinals=true forwarders=true dynamic=true files=true heap=true; ...
```

`tools/qemu_boot_test.sh` pins the control-3b line, the heap self-test line's
opening and its "both remaining blocks freed (1 and 1)" clause, the new
`heap=true` on the checks line, and the 18-name/18-import load lines.

**Measured.** `kernel32.dll` `.text` `0x210 -> 0x260` (three stubs appended last:
`GetProcessHeap` at RVA `0x11ED`, `HeapAlloc` `0x1202`, `HeapFree` `0x122C`, so
every earlier RVA is unchanged); the client's `.idata`/`.text` grew to import
all three; kernel32 declares 18 exports; the client imports all 18 and raises
18 refusals (the previous 13 plus double free, foreign handle, exceptions flag,
oversize request and unissued free).

**Two build defects this increment exposed, both fixed:**

1. binutils 2.45's PE linker emits the synthesized `.idata` as READONLY, so the
   loader correctly refused the client with `ImportSectionNotWritable` (2.42 set
   the flag). `tools/patch_pe_idata.py` restores `IMAGE_SCN_MEM_WRITE` -- the
   section the loader writes at load time SHOULD declare it -- and the build
   runs it after the client link, so the artifact is correct on either ld. The
   loader's rule was not loosened.
2. The PIE fixture's RELRO premise ("a default `ld -pie` build always has one")
   is true of a Linux-targeted ld and false of the generic `x86_64-elf` target
   on a Windows host; `build.sh` now states `-z relro` explicitly instead of
   inheriting a host-dependent default.

**Limits.** One heap, one handle, a bump allocator with an eight-entry live-block
table, first-fit reuse without splitting, no coalescing, no `HeapReAlloc`,
`HeapSize`, `HeapValidate` or `HeapCreate`, no low-fragmentation-heap API, and
no per-thread last error (still one cell per kernel, as v0.2 stated). The region
is mapped by the boot proof rather than requested by the program, so there is no
growable heap yet. `HEAP_GENERATE_EXCEPTIONS` is refused by design; a client
that needs it needs the exception dispatcher this kernel does not have.
**The whole chain on a non-Linux linker target.** Two host-dependent
assumptions kept the real `tools/qemu_boot_test.sh` gate from reaching the
Win32 proof on this Windows host, and both were layout assumptions rather
than kernel behavior:

1. The generic `x86_64-elf` ld does not emit PT_GNU_RELRO for `ld -shared`
   by default, while four demos treat that region as their premise -- and
   the loader's relocation pass ran only inside the RELRO grant window, so
   a relro-less provider's GOT stayed zeroed and its own code faulted at
   0x0. `build.sh` now states `-z relro` on every shared-object fixture
   those demos load, and `elf::load` applies relocations for an object with
   no RELRO region too (no grant/revoke needed: its segments are mapped
   exactly as the file declared, and an unsatisfiable entry is a load
   failure, not a silent zero slot).
2. The demos pinned the slot offsets only a Linux-targeted linker produces
   (0x2fe0/0x3008, 0x2fd8, 0x2fe8, 0x1016). They now locate slots through
   `elf::find_symbol_slots` (symbol + relocation KIND, including
   DT_JMPREL's JUMP_SLOT), read a slot's linker-baked initial value from the
   FILE's own PT_LOAD bytes (`elf::read_file_u64_at_vaddr`), and take
   `.got.plt` from DT_PLTGOT. On this host the dependent's measured layout
   is RELRO 0x1000..0x2000, GLOB_DAT slots 0x1170/0x1178, flat slot 0x2008,
   and JUMP_SLOT 0x2000 -- just outside the region, so lazy binding stays
   possible.

`tools/qemu_boot_test.sh` also assembles its ISO with `grub-mkimage` +
`xorriso` when the host has no `grub-mkrescue` (the route `make_live_iso.sh`
already used); `gate_env.sh` already classed that as a usable fallback. With
these, the real gate runs on this host and reaches the Win32 proof
(`heap=true`), `PASS`.

**A direct pin on the relocation rule.** The RELRO-less relocation path is
now checked on its own artifact, not only as a side effect of other demos'
providers: `build.sh` links `prog_dyn.s` a second time WITHOUT `-z relro`
(`userprog/userprog_dyn_norelro.elf`, verified to carry no PT_GNU_RELRO),
and `elf_dyn_demo` loads it at 0x64000080000 with no resolver and checks
all three kinds it carries -- GLOB_DAT, R_X86_64_64 and the symbol-less
R_X86_64_RELATIVE. Every boot prints:

  experimental elf-dyn-demo: the same .so linked WITHOUT -z relro loaded at
  0x64000080000: GNU_RELRO outcome absent (None), GLOB_DAT slot
  0x64000081380 == 0x640000813a0 and R_X86_64_64 slot 0x640000813a8 ==
  0x640000813a0 (resolved dyn_value), RELATIVE slot 0x640000813b0 ==
  0x640000813b8 (base + addend) -- OK, relocations are not gated on a
  RELRO region

and `tools/qemu_boot_test.sh` requires that line.

**The matching negative control.** `build.sh` also links
`userprog/prog_norelro_bad.s` WITHOUT `-z relro`: a relro-less object whose
only dynamic relocation is a GLOB_DAT against a symbol it does not define.
`elf_dyn_demo` offers it to `elf::load` with no resolver and requires the
typed refusal -- a loader that skipped relocations whenever RELRO was absent
(the old shape) would have returned success with the slot left zero. Every
boot prints:

  experimental elf-dyn-demo: the relro-less NEGATIVE CONTROL (one GLOB_DAT
  against an undefined symbol, loaded with no resolver) was refused with
  UndefinedSymbol -- OK, an unsatisfiable relocation inside a relro-less
  object fails the load instead of leaving a zeroed slot

and `tools/qemu_boot_test.sh` requires that line too.

## The DEX string/type/class tables: what a class is called, and seven refusals (2026-11-25)

The parser could describe a DEX file's SHAPE -- the header's section counts and a
`map_list` that names itself -- but not read anything the file contained: every
fixture had to have zero strings, zero types and zero classes for the proof to
pass. This increment adds the three tables an app's identity lives in, and a
fixture that actually has them.

`userprog/classes_hello.dex` (294 bytes, sha256 `6e44ca8d...28ac4`, regenerated
byte-identically by `tools/make_dex_fixture.py`): three strings
(`Lnyrqis/hello/Hello;`, `Ljava/lang/Object;`, `Hello.java`), two type ids, one
class def (public, superclass type 1, source-file string 2), the required
`map_list` with six entries, and correct Adler-32 + SHA-1. The generator writes
the signature BEFORE the checksum because the checksum's range (`bytes[12..]`)
contains the signature field; the first run computed them the other way round and
produced a file whose checksum no reader would accept. That is the same order the
kernel's `verify` checks, and it is why the fixture script asserts both fields
before it writes.

`dex::string` reads one `string_data_item`: a ULEB128 UTF-16 length (bounded to
five bytes and to `u32`, with the accumulator in `u64` so an over-range value is
detected rather than truncated), MUTF-8 bytes validated against the format's
actual shapes (one-byte ASCII, the two-byte NUL, two-byte `C2..DF`, three-byte
`E0..EF` including surrogates), a NUL terminator required inside the file, and
the decoded code-unit count matched against the declared length.
`type_descriptor` resolves a `type_ids` entry through the string pool (checking
the string index before it uses it), `class_def` resolves a class's own type,
superclass and source-file string (`NO_INDEX` is the only "none"), and
`class_descriptor` requires the class's descriptor to be shaped like one
(`L...;`).

Every malformed-table shape is a permanent boot check on a copy of the real
fixture with exactly one field broken, so a rejection cannot be explained by any
other check. The first gate run caught that one of them was aimed at the wrong
byte: overwriting the LAST string's terminator does not leave the string
unterminated, because the scan runs on into the `map_list` and finds a NUL there
(refusing with `StringLengthMismatch`, not `StringNotTerminated`). The control
now truncates the file at the terminator, which is the case it claims to test.
Verified boot transcript (`tools/qemu_boot_test.sh`, passing):

```
  experimental dex-demo: class tables -- a 294 byte DEX with 3 string(s), 2 type(s), 1 class(es): string 0/1/2 answer "Lnyrqis/hello/Hello;"/"Ljava/lang/Object;"/"Hello.java", type 0/1 resolve them as descriptors, and class 0 is Lnyrqis/hello/Hello; (public, flags 0x1) extending Ljava/lang/Object; with source file Hello.java -- OK
  experimental dex-demo: table refusals -- a string_data offset past EOF refused StringDataOutOfBounds, an over-long ULEB128 refused BadUleb128, a declared UTF-16 length of 19 for 20 units refused StringLengthMismatch, an unterminated string refused StringNotTerminated, a type index past the table refused TypeIndexOutOfBounds, a class index past the table refused ClassIndexOutOfBounds, and a class descriptor that does not start with L refused BadTypeDescriptor -- OK
  experimental dex-demo self-test: crypto known-answer OK, valid parse+integrity OK, bad-magic rejection OK, map-self-reference rejection OK, checksum rejection OK, class tables OK, table refusals OK -- OK
```

The gate pins those three lines. **Scope is unchanged and stated rather than
implied:** these are the tables' CONTENTS. There is still no `class_data`, no
proto/field/method table, no annotation, no APK (ZIP) unwrapping and no
bytecode execution of any kind -- each of those is the next table, not a hidden
part of these.

## The Win32 API surface v1.2: HeapSize and HeapReAlloc -- a CRT's realloc (2026-11-25)

The heap could allocate and free, but a C runtime's `realloc` had no path: a
program could not ask how large a block was, nor change one's size while keeping
its bytes. kernel32's 19th and 20th exports close that gap, and the interesting
part is not the two stubs but what the allocator honestly does.

- `HeapSize(hHeap, dwFlags, lpMem)` answers the BLOCK's size, not the requested
  size. This allocator rounds to 16 bytes and does not split a reused block, so
  the answer can be larger than the request that produced it -- Windows
  documents the same ("may be larger than requested"). A freed or never-issued
  address is `ERROR_INVALID_ADDRESS`, by the same evidence rule `HeapFree`
  applies: a returned block is no longer a block.
- `HeapReAlloc(hHeap, dwFlags, lpMem, dwBytes)` has the two shapes Windows has.
  A request that fits the block answers the SAME pointer (no split, no move);
  a request that needs more takes a new block (a freed one that fits, else the
  bump pointer), copies the old block's bytes, frees the old block, and -- with
  `HEAP_ZERO_MEMORY` -- clears the bytes beyond it, the part the caller has
  never seen. `HEAP_REALLOC_IN_PLACE_ONLY` refuses the move instead of making
  it, which is the one thing that flag exists to prevent, and reports
  `ERROR_NOT_ENOUGH_MEMORY`. `HEAP_GENERATE_EXCEPTIONS` stays refused for the
  same reason `HeapAlloc` refuses it. The shared `heap_take`/`heap_live_block`
  helpers keep `HeapAlloc`, `HeapReAlloc` and `HeapSize` from drifting apart
  about the block table and the region.

**A wrong expectation the gate caught, and why the allocator was right.** The
first ring-3 proof asked for 48 bytes from a 32-byte block and asserted the new
block's `HeapSize` was 48. It is 128: the reallocation reused a freed 128-byte
block left by the kernel-side control, and a reused block keeps its own size --
the allocator's stated no-split limit. The transcript's prose said "answered 48"
while the boolean was false, so the checks line read `realloc=false` and the gate
failed on the needle. The fix was to the EXPECTATION and the prose, not to the
allocator: the client now asserts 128, and the line says why. This is the
failure mode the checks-line boolean exists to expose.

**Verified from both sides in the same boot.** Kernel-side control 3c checks the
region, the copy and the refusal without ring 3; the ring-3 client repeats the
sequence with its own addresses, writes a byte through the old block and reads it
back through the new one, proves a shrink answers the same pointer, and asks for
every refusal shape.

```
  experimental win32-demo: negative control 3c -- a 64-byte block's HeapSize answered 64, HeapReAlloc(128) moved it and kept the byte written through it, the new block's HeapSize answered 128, an in-place-only grow was refused as HeapExhausted with ERROR_NOT_ENOUGH_MEMORY (8) leaving the block at 128, and HeapSize of the moved-from address was refused as HeapBlockNotLive -- OK, size and reallocation are the heap's own bookkeeping
  experimental win32-demo: the heap's size and reallocation -- HeapSize of the live 32-byte block answered 32 (1); the client wrote 0x5a through it and HeapReAlloc(48) answered a DIFFERENT block 0x4d0000100060 with the byte kept (1), whose HeapSize answered the REUSED block's own size 128 rather than 48, the stated no-split limit (1); a shrink to 16 answered the same pointer (1); an in-place-only grow to 4096 answered 0x0 with ERROR_NOT_ENOUGH_MEMORY (8), HEAP_GENERATE_EXCEPTIONS answered 0x0 with ERROR_INVALID_PARAMETER (87), and HeapSize of the moved-from block answered 0x0 with ERROR_INVALID_ADDRESS (487) -- OK, a CRT's malloc/realloc/free triple now runs through this kernel
  experimental win32-demo self-test checks: console=true hello=true bytes=true empty=true closed=true miss=true errors=true monotonic=true exit=true handles=true refusals=true handle_test=1 startup=true bindings=true modules=true ordinals=true forwarders=true dynamic=true files=true heap=true realloc=true; ...
```

`tools/qemu_boot_test.sh` pins the control-3c line, the heap line's new clauses,
`realloc=true`, and the 20-name/20-import load lines; the client's run raises 21
refusals (the previous 18 plus the in-place grow, the exceptions flag and the
moved-from `HeapSize`).

**Measured.** `kernel32.dll` `.text` `0x260 -> 0x2c0` (two stubs appended last:
`HeapSize` at RVA `0x1256`, `HeapReAlloc` at `0x1280`, so every earlier RVA is
unchanged); `.edata` `0x1fb` with 20 names; client `.text` `0xef0`, `.idata`
`0x31c`, 20 imports, `SizeOfImage 0x4000` and ImageBase unchanged;
`build.sh` pins the new `.text` window in both linker layouts.

**Limits, unchanged and extended by one.** One heap, one handle, a bump allocator
with an eight-entry live-block table, first-fit reuse WITHOUT splitting or
coalescing (so a reused block keeps its size and a shrink does not release
anything), no `HeapCreate`/`HeapValidate`/`HeapSize` for a foreign heap, no
growable heap, and a single process-wide last-error cell. `HeapReAlloc`'s grow
path copies and frees rather than extending in place -- which is why
`HEAP_REALLOC_IN_PLACE_ONLY` is a real refusal here instead of a promise.

## The DEX method tables: proto_ids, method_ids and class_data (2026-11-25)

The parser could name a class but not read what it declares: `class_data_off`
was reported as a number and nothing followed it, so every method in an app was
invisible. This increment resolves the three tables a method lives in, which is
the first point at which the parser answers a signature.

**The fixture.** `userprog/classes_methods.dex` (436 bytes, sha256
`bf5f93a1...feb1299`, checksum `0x035a351c`, SHA-1 `68b189b1...b87b4cc7`,
regenerated byte-identically by `tools/make_dex_fixture.py`): seven strings,
four types, one `proto_id`, one `method_id`, one class, a `type_list` and a
`class_data`. The class is still `Lnyrqis/hello/Hello;` and now declares
`main([Ljava/lang/String;)V` as `ACC_PUBLIC|ACC_STATIC|ACC_NATIVE` (0x109) --
native so that `code_off == 0` is a CORRECT file rather than a malformed one,
since this increment is about signatures and there is no `code_item` parser yet.
The map_list grew to ten entries, each asserted to start at a 4-byte-aligned
offset before the file is written.

**What each resolver proves, not just reads.**

- `proto_id` resolves the shorty string, the return type and the parameter list,
  and then checks the three against each other: the shorty must have exactly one
  character per return value plus one per parameter (`ShortyLengthMismatch`), and
  every character must be a shorty descriptor (`BadShortyDescriptor`). A proto
  whose string and type list disagree is refused rather than reported, which is
  the difference between copying three indices and proving a signature.
- `method_id` reads the record at its real widths -- `u2 class_idx`, `u2
  proto_idx`, `u4 name_idx` -- and resolves all three: the declaring class's
  descriptor, the proto, and the name. Reading it as three `u32`s (the first
  version of the fixture generator did) is off by four bytes and is the mistake
  this width-by-width read exists to make impossible.
- `type_list` bounds the declared size against the file BEFORE allocating, then
  resolves every entry through `type_descriptor`.
- `class_data` parses the four ULEB128 counts in the format's order (static
  fields, instance fields, direct methods, virtual methods) and then checks each
  group's count against the bytes actually left -- two per member minimum --
  before allocating, refusing `ClassDataTruncated` rather than trusting a count
  it cannot fill. Method indices are resolved through `method_id`; field indices
  are range-checked against `field_ids_size` (`FieldIndexOutOfBounds`) but NOT
  name-resolved, because `field_ids` is still absent and this module says so.

**Nine malformed method-table shapes are permanent boot controls**, each on a
copy of the real fixture with exactly one field broken. The gate's first run of
this increment caught a real mislabel rather than a code defect: the fixture
declared its parameter as `Ljava/lang/String;` while the proof (and the gate
needle) claimed `[Ljava/lang/String;`, so the composed signature came out
`main(Ljava/lang/String;)V` and the line said FAILED. The fixture now declares
the array descriptor and the signature is `main([Ljava/lang/String;)V`. That is
the checks-line boolean doing its job: the prose read plausibly either way.

Verified boot transcript (`tools/qemu_boot_test.sh`, passing, GATE_RC=0):

```
  experimental dex-demo: method tables -- a 436 byte DEX with 1 proto(s) and 1 method(s): proto 0's shorty "VL" matches return type V and 1 parameter [Ljava/lang/String;, method 0 is main on Lnyrqis/hello/Hello; with signature main([Ljava/lang/String;)V (flags 0x109, ACC_PUBLIC|ACC_STATIC|ACC_NATIVE), listed in class_data with 0 static / 0 instance field(s) and 1 direct / 0 virtual method(s) -- OK
  experimental dex-demo: method refusals -- a proto index past the table refused ProtoIndexOutOfBounds, a method index past the table refused MethodIndexOutOfBounds, a class_data offset past EOF refused ClassDataOutOfBounds, a class_data whose counts overrun the file refused ClassDataTruncated, a parameter list offset past EOF refused TypeListOutOfBounds, a parameter type past the type table refused TypeIndexOutOfBounds, a shorty disagreeing with its parameter count refused ShortyLengthMismatch, a class_data field index past field_ids refused FieldIndexOutOfBounds, and a class_data method index past method_ids refused MethodIndexOutOfBounds -- OK
  experimental dex-demo self-test: crypto known-answer OK, valid parse+integrity OK, bad-magic rejection OK, map-self-reference rejection OK, checksum rejection OK, class tables OK, table refusals OK, method tables OK, method refusals OK -- OK
```

`tools/qemu_boot_test.sh` pins that line's fixture size, shorty clause, composed
signature and flags, three of the refusal clauses, and the self-test line's new
tail, so a regression in any of them fails the gate instead of passing quietly.

**Then absent, and named:** `field_ids` names and `code_item` -- both added the
next day (below). Annotations, encoded arrays, static values and the string
pool's de-duplication rules remain absent, and the MEANING of a code_item's
words is still out of scope: `class_data` answers WHICH methods exist and what
they look like, not what they do.

## The Win32 file cursor: GetFileSize and SetFilePointer (2026-11-26)

The read-only volume could stream a file but could not say how long it was or let
a program choose where to read. Two exports close both gaps, and neither invents
state: `GetFileSize(hFile, lpFileSizeHigh)` answers the byte count of the volume
entry the handle is open on (writing the optional high dword, because a caller
that passed a pointer reads it), and `SetFilePointer(hFile, lDistanceToMove,
lpDistanceToMoveHigh, dwMoveMethod)` moves the VERY cursor `ReadFile` advances --
by a signed distance from the file's start, the current position, or its end --
and answers where it now is.

The signed distance is the part a hand-written reader gets wrong first.
`lDistanceToMove` is a `LONG`, so with `lpDistanceToMoveHigh == NULL` the low
dword is read as an `i32` before widening; read as unsigned, `-4` from `FILE_END`
would seek to 4294967292 instead of four bytes back. With a pointer, the two
halves are reassembled as a signed 64-bit value and the new high dword is written
back on success. The volume owns the arithmetic in `i128`, refuses a result
before the first byte with `NegativeSeek` (Windows' `ERROR_NEGATIVE_SEEK`) and
leaves the cursor UNTOUCHED, and allows a position past the end, where the next
read answers zero bytes. That last case closed a latent panic in `read`, which
used to slice `data[start..]` and would have faulted for `start > len`.

What makes it a proof rather than two more arms is that the client's reads
change with the cursor. `tools/qemu_boot_test.sh` pins:

```
  experimental win32-demo: resolved 22 import(s) from the client's REAL .idata against kernel32.dll's REAL export directory (CloseHandle, CreateFileA, ExitProcess, FreeLibrary, GetCommandLineA, GetFileSize, GetLastError, GetModuleFileNameA, GetModuleHandleA, GetProcAddress, GetProcessHeap, GetStdHandle, GetTickCount, HeapAlloc, HeapFree, HeapReAlloc, HeapSize, LoadLibraryA, ReadFile, SetFilePointer, SetLastError, WriteFile), every slot reading back its export address -- OK
  experimental win32-demo: a file's size and cursor -- GetFileSize(hello.txt) answered 33 byte(s) with the high dword written as 0 (true), SetFilePointer(3, FILE_BEGIN) answered 3 and the read there answered 4 byte(s) reading "lo f" (true), a zero move from FILE_CURRENT answered 7 -- where the read left it (true), a MOVE OF -4 FROM FILE_END answered 29 and the read there answered "me..." (true), a seek to -100 answered 0xffffffff with ERROR_NEGATIVE_SEEK (131) leaving the cursor at 33 (true), an unknown move method answered 0xffffffff with ERROR_INVALID_PARAMETER (87), and GetFileSize of the console handle answered 0xffffffff with ERROR_INVALID_HANDLE (6) -- OK, a program can ask a file how big it is and read from a position it chose
  experimental win32-demo self-test checks: console=true hello=true bytes=true empty=true closed=true miss=true errors=true monotonic=true exit=true handles=true refusals=true handle_test=1 startup=true bindings=true modules=true ordinals=true forwarders=true dynamic=true files=true heap=true realloc=true cursor=true; ...
```

The three seek shapes are deliberately different questions: `FILE_BEGIN` places
the cursor absolutely, `FILE_CURRENT` with a zero distance reads back where the
PRECEDING READ left it (7), and `FILE_END` with `-4` counts backwards from the
length -- so a cursor that only pretended to move changes what the program sees.
The two failure shapes are Windows': a negative result is a VALUE
(`INVALID_SET_FILE_POINTER`, `0xffffffff`) plus `ERROR_NEGATIVE_SEEK`, with the
cursor still at 33, while a move method this volume does not implement is a
typed refusal (`ERROR_INVALID_PARAMETER`) and `GetFileSize` of the console
handle is refused as `ERROR_INVALID_HANDLE`. `EXPECTED_REFUSALS` therefore rose
from 21 to 23, and every refusal still asserts the code it maps to.

Three things the boot caught, each fixed at cause:

- The existing "index 20 is not an export" control became false the moment
  `GetFileSize` was the 21st export. The control now derives the index from
  `EXPECTED_EXPORTS.len()`, so the next appended stub cannot make it wrong
  again -- the same class of drift the gate has caught before.
- `win32_console.exe`'s `SizeOfImage` grew from `0x4000` to `0x6000` (two
  imports and their call sites), so `build.sh`'s pin and the demo's
  `APP_IMAGE_SIZE` moved with it.
- The import listing in the transcript was HARD-CODED in usage order, while the
  client's `.idata` is alphabetical. Nothing compared the two, so the line was
  quietly wrong. It is now built from the import walk itself, and the mismatch
  showed up immediately (the gate's needle refused the old order).

Stated limits: a successful seek to exactly `0xFFFFFFFF` is indistinguishable
from the failure sentinel -- on Windows too, which is why `SetFilePointer` is
always followed by a `GetLastError` check; this volume's files are far too small
to reach it. `dwShareMode`, the security attributes, the flags/template of
`CreateFileA` and `lpOverlapped` remain as stated before: accepted-and-ignored
or refused, never pretended.

## The DEX field and code tables: field_ids, code_item, and the deltas that make them reachable (2026-11-26)

The method tables answered signatures but not FIELDS, and had no way to reach a
method's body. This increment adds `field_id` (declaring class, type, name) and
`code_item` (the register frame and the raw 16-bit code units), and in doing so
fixes two format rules the earlier fixture was too small to expose:

- **`class_data` indices are DELTAS.** Each `field_idx_diff`/`method_idx_diff` is
  a delta from the previous entry in the SAME list, and every list restarts from
  its own. `class_members` now accumulates them (saturating, so an overflow
  lands outside the table and is refused) and reports ABSOLUTE indices. A
  reader that treated a delta as an index is right only while the deltas are 0
  or 1; a reader that forgot to reset between lists adds one list's last index
  to the next list's first delta -- both produce plausible numbers, which is why
  the new fixture has three members per group and an instance field whose delta
  is 2 (the static list ends at 1, so a non-resetting reader computes 3 and is
  refused).
- **An `encoded_method` is THREE ULEB128s** -- `method_idx_diff`,
  `access_flags`, `code_off` -- while an `encoded_field` is two. The earlier
  fixture wrote two per method, which parsed only because nothing followed it.
  The reader now takes all three, refuses a method whose flags and `code_off`
  disagree (`MethodCodeMismatch`: a native or abstract method must have no body,
  and an ordinary method must have one -- checked in BOTH directions), and parses
  every non-zero `code_off` immediately so an entry cannot carry a body nothing
  validated.

A fourth real fixture, `userprog/classes_fields.dex` (604 bytes, sha256
`e13618a9b08804f76110a852857f7b699bcd4761fc76207afd5d9d9ec8007a63`, built
deterministically by `tools/make_dex_fixture.py`), declares three fields
(`COUNT:I`, `DEPTH:I` static; `name:Ljava/lang/String;` instance), three direct
methods (`size()I` and `run()V` with real `code_item`s, `make()V` native with
`code_off` 0), and two code items whose words are hand-checkable: `0x0012` is
`const/4 v0, #0` and `0x000f`/`0x000e` are `return v0`/`return-void`. The older
`classes_methods.dex` is unchanged byte for byte (its added `code_off` zero
replaced padding the map already needed), and `classes_hello.dex` keeps its
pinned digest.

Verified boot transcript (`tools/qemu_boot_test.sh`, passing, GATE_RC=0):

```
  experimental dex-demo: field and code tables -- a 604 byte DEX with 3 field(s), 3 method(s) and 2 code_item(s): field 2 composes to Lnyrqis/hello/Fields;.name:Ljava/lang/String;, class_data answers static field indices [0, 1], instance [2] (its own delta, not the static list's accumulator) and direct method indices [0, 1, 2], size()I carries a 2-word code_item at 0x194 reading [0x0012, 0x000f] (const/4 v0, #0; return v0) and run()V one at 0x1a8 writing and returning void, while make()V is ACC_NATIVE with code_off 0 -- OK
  experimental dex-demo: member refusals -- a field index past field_ids refused FieldIndexOutOfBounds, a static delta landing past it refused FieldIndexOutOfBounds, a native method carrying a non-zero code_off refused MethodCodeMismatch before the offset was resolved, an ordinary method with none refused MethodCodeMismatch, an ABSTRACT method carrying one refused MethodCodeMismatch (while a native method without one is accepted OK), a code_off past EOF refused CodeItemOutOfBounds, a method delta landing past method_ids refused MethodIndexOutOfBounds, a code_item whose words run past EOF refused CodeItemTruncated, one whose ins_size exceeds its registers refused CodeItemRegisterMismatch, an entry that stops before its code_off refused ClassDataTruncated, a class_data replacement one byte short, one byte long or an encoding grown past the span refused by the span rule, and code_item(0) refused CodeItemOutOfBounds -- OK
  experimental dex-demo self-test: crypto known-answer OK, valid parse+integrity OK, bad-magic rejection OK, map-self-reference rejection OK, checksum rejection OK, class tables OK, table refusals OK, method tables OK, method refusals OK, field and code tables OK, member refusals OK -- OK
```

The gate caught the ordering consequence of the new flag rule: the older control
`method_index_past` corrupts an INDEX and leaves the flags alone, so with
"flags and code_off disagree" checked first it answered `MethodCodeMismatch`
instead of `MethodIndexOutOfBounds`. The index is the more fundamental question
-- an entry that names no method is malformed whatever its flags say -- so
`method_id` is now resolved BEFORE the flags/code rule, and the old control
passes again for the reason it names.

**The controls now state their own width.** `fields_class_data` returns
`Option<Vec<u8>>`: it refuses an encoding that grew past the fixture's 22-byte
`class_data` span and otherwise pads to exactly that length, and
`with_class_data` refuses any replacement that is not exactly the span. Before
this, the splice cut a longer encoding with `min()` and padded a shorter one, so
a control whose variant did not fit was still tested -- against a file that was
malformed for a second, unstated reason. Two of them were in that state: "a
native method carrying a code_item" and its `ACC_ABSTRACT` twin each put a
two-byte body offset on top of two-byte flags and a one-byte delta, which is 23
bytes, and both were running on a 22-byte cut. They now carry a one-byte
non-zero `code_off` (1): the same rule, at the fixture's own width, and it pins
the ORDER of that rule -- offset 1 is inside the file but is not a `code_item`,
so a reader that resolved the offset before the flags would answer
`CodeItemTruncated` instead of `MethodCodeMismatch`. The span rule is itself a
boot control (a replacement one byte short, one byte long, or an encoding grown
past the span is refused), and a clause that does not refuse now prints "NOT
refused" rather than "ACCEPTED", since with the span rule in place "the file
was never built" and "the parser took it" are both `false` and the word must not
claim the second.

The gate caught exactly the state this rule exists to make audible: the first
run with it printed "a native method carrying a code_item NOT refused" and
FAILED the line (and the DEX self-test with it), before either control had been
re-expressed at the right width.

**Still absent, and named:** the MEANING of the code units `code_item` returns
(decoding, verification and execution are an interpreter or an AOT compiler -- a
different undertaking, not a table this module forgot), annotations, encoded
arrays, static values, and the string pool's de-duplication rules. `class_data`
and `code_item` answer what a method's body IS, not what it does.

## The DEX annotations tables and encoded arrays: two item families, one header byte (2026-11-26)

A `class_def_item` names two more item families beyond its member table, and both
were still at zero in every fixture: `annotations_off` and `static_values_off`.
They are now resolved.

**The annotation tables are three levels deep, and the parser reads all of them.**
`class_def.annotations_off` names an `annotations_directory_item` -- a
`class_annotations_off` plus three counts (fields, methods, parameters) and then
that many `(member_idx, annotations_off)` rows. Each of those names an
`annotation_set_item` (a count plus item offsets), and each of THOSE names an
`annotation_item`: a visibility byte plus an `encoded_annotation` (type index,
element count, then `(name_idx, encoded_value)` pairs). `annotations_directory`
returns the annotations themselves, so no caller ever holds an offset it must
remember to resolve; along the way the visibility byte is checked against the
format's three values (BUILD/RUNTIME/SYSTEM), the annotation's type index through
`type_ids`, every element name through the string pool, and a field row's index
through `field_ids` and a method or parameter row's through `method_ids` -- so a
directory cannot annotate a member the file's own tables do not have.

**`encoded_array_item` is a count plus `encoded_value`s, and every value form the
format defines is decoded**: `byte`, `short`, `char`, `int`, `long`, `float`,
`double`, `method_type`, `method_handle`, `string`, `type`, `field`, `method`,
`enum`, `array`, `annotation`, `null` and `boolean`. The index forms are resolved
through the table they name before the value is returned. Two are deliberately
raw: floats keep their bit pattern (a parser has no business rounding a value it
only validates, and it keeps `Eq`), and `method_handle` keeps its index because
this module has no method-handle table to check it against.

**The byte that matters is the value header**, which packs the value type in its
low five bits and `width - 1` in its top three -- and the width is what says how
many bytes to read, so it is checked against the type's own maximum before it is
used. Two types do not follow `width - 1`, and both are stated in their own
match arms: `byte` is fixed at one byte, and `VALUE_BOOLEAN`'s header field IS
its byte count (0 = false, 1 = a byte holding true).

**Nesting is inline, so depth is a property of the bytes.** A `VALUE_ARRAY`'s
value is an `encoded_array` written in place, not an offset, so a reader that
recursed without a bound could be driven off its stack by a small file. The
format states no limit; this parser states its own (`MAX_ENCODED_VALUE_DEPTH = 8`)
and refuses past it, the same kind of decision as refusing a ULEB128 longer than
five bytes.

A fifth real fixture, `userprog/classes_annotations.dex` (724 bytes, sha256
`179acbcc538503b55845b520cbc4d7609c4356f3420a5d1b325355e51d51ae12`, built
deterministically by `tools/make_dex_fixture.py`), carries one class whose
annotation is `VISIBILITY_RUNTIME` on `Lnyrqis/hello/Marker;` with three elements
-- `count` = 7 (an `int`), `name` = `"hello"` (a `string`), and `flags` =
`[true, false, ["hello"]]` (two levels of nested array) -- plus one annotation
each on a field (BUILD), a method (SYSTEM) and that method's parameters
(RUNTIME), and a `static_values` array giving the class's two static fields their
initial values (`SEED` = 1000, stored as a TWO-byte `VALUE_INT`, and `label` =
`"hello"`). Its four annotation sets and four annotation items are laid out
contiguously, because a `map_list` names ONE offset and a count per item type --
interleaving them would make the file non-conformant in a way the map check does
not catch. The four earlier fixtures regenerate byte for byte.

Verified boot transcript (`tools/qemu_boot_test.sh`, passing, GATE_RC=0):

```
  experimental dex-demo: annotation tables -- a 724 byte DEX whose class Lnyrqis/hello/Annotated; names an annotations directory with 1 class, 1 field, 1 method and 1 parameter row(s): its class annotation is VISIBILITY_RUNTIME on Lnyrqis/hello/Marker; with elements count=7 (an int), name="hello" (a string) and flags=[true, false, ["hello"]] (nested arrays two deep), its field/method/parameter rows resolve to a BUILD item, a SYSTEM item and a RUNTIME item on member 0, and its static_values encoded array reads SEED=1000 -- the two-byte VALUE_INT whose width field a reader that ignored it would have read as 232 -- then label="hello"; classes_fields.dex, whose class names no directory and no static values, answers None and an empty array -- OK
  experimental dex-demo: annotation refusals -- a class_def annotations_off past EOF refused AnnotationsDirectoryOutOfBounds, a directory whose field count overruns the file refused AnnotationsDirectoryTruncated, a class_annotations_off past EOF refused AnnotationSetOutOfBounds, a set asked at offset zero refused AnnotationSetOutOfBounds, a set whose size overruns the file refused AnnotationSetTruncated, an annotation item past EOF refused AnnotationItemOutOfBounds, a visibility byte of 3 refused BadAnnotationVisibility, a value type 0x07 refused BadEncodedValueType, an int value claiming seven bytes refused BadEncodedValueArg, an annotation type index past type_ids refused TypeIndexOutOfBounds, a string value naming string 127 refused StringIndexOutOfBounds, a directory row naming field 5 refused FieldIndexOutOfBounds, a static_values_off past EOF refused EncodedArrayOutOfBounds, an array whose count overruns the file refused EncodedArrayTruncated, and arrays nested nine deep refused EncodedValueTooDeep -- OK
  experimental dex-demo self-test: crypto known-answer OK, valid parse+integrity OK, bad-magic rejection OK, map-self-reference rejection OK, checksum rejection OK, class tables OK, table refusals OK, method tables OK, method refusals OK, field and code tables OK, member refusals OK, annotation tables OK, annotation refusals OK -- OK
```

**What the first run caught, and why it is recorded here.** The increment's first
boot printed `annotation tables -- ... -- FAILED` and `3` clauses of the refusal
line as `ACCEPTED`, and the kernel panicked on the failing self-test. One real
parser bug and two mis-built controls were behind it:

- The `VALUE_BOOLEAN` arm read `width + 1` bytes instead of `width`, so a
  `[true, false, ...]` array consumed the NEXT value's header as a payload byte
  and then hit `0x05` -- an undefined type -- a few bytes later. The refusal that
  fired was `BadEncodedValueType`, naming the wrong table entirely. Fixed by
  reading `width` bytes for booleans, which is the format's own rule, and stated
  in the module doc as the exception it is.
- "an array whose count overruns the file" claimed 64 values where 551 bytes were
  left. A count of 64 is one the file CAN hold, so the parse went on and failed
  later for a different reason. The control now claims 639 (`0xff 0x04`) against
  those 551 bytes.
- "arrays nested nine deep" was passed offset 0, and `encoded_array(0)` is the
  format's "no static values" -- it answers an empty array. The control was
  measuring the absent case. It now reads at offset 1 behind a pad byte.

Because a boot is the expensive way to find that out, the parser was also built
against a host harness (`nykernel-package/target/dexhost`, a throwaway binary that
`#[path]`-includes the same `src/experimental/dex.rs` and stubs the two crypto
calls), which reproduced all three failures in seconds and then confirmed all 21
positive and control cases before the next boot. It is in a gitignored `target/`
directory and is not part of the build.

**Still absent, and named:** an annotation's MEANING (a `VALUE_ENUM`'s field is
resolved as a field, not proven to be an enum constant; `VALUE_METHOD_HANDLE` is
carried raw for want of a method-handle table; a parameter annotation's element
count is not compared to a proto's parameter list) and the string pool's
de-duplication rules.

*Superseded in part by the 2026-11-27 increment below: `VALUE_METHOD_HANDLE` now
resolves through the method-handle table, the parameter row goes through an
`annotation_set_ref_list`, and the annotations fixture is 744 bytes. The
transcript above is the run it was taken from.*

## The DEX map as a table of contents, and the three sections only it names (2026-11-27)

`parse` used to walk the `map_list` only to find its own entry. The map is now
read as DATA -- a `map_list` resolver returning typed `MapEntry`s -- and that
reading is what makes three of the format's sections reachable at all:
`call_site_ids` and `method_handles` have no header field (they arrived in
version 038), and a parameter row's `annotation_set_ref_list` is a section in
its own right.

**Every entry is checked against the file, not just counted.** A type outside
the format's twenty (`UnknownMapItemType`); offsets that do not strictly
increase (`MapNotSorted`); a fixed-size section whose `size * item size` runs
past EOF (`MapSectionOutOfBounds`); a `header_item` entry other than one item at
offset zero, a `map_list` entry other than this one, or a header-declared table
described at another offset or with another count (`MapSectionMismatch`); and a
map that stops describing itself (`NoMapListEntry`). The formula in the format's
"Item Size In Bytes" column is the extent of ONE list of `size` entries, so it
is used ONLY for the nine fixed-size id tables; for the list-shaped data items
(`type_list`, both annotation-set forms, `map_list`) a map entry counts ITEMS,
and a section of four one-entry sets is legal -- applying the formula there
would refuse a conformant file.

**The two map-only id tables now resolve.** `method_handle_item` is
`(handle_type, unused, field_or_method_id, unused)`, and the type is the rule:
0x00..=0x03 are field accessors and 0x04..=0x08 method invokers, so the id is
resolved through `field_ids` or `method_ids` accordingly. `call_site_id_item`
names an `encoded_array_item`, which must be the three-element definition the
format gives -- `(method handle, method name, method type)` -- and each element
is resolved by the existing value reader, which means `VALUE_METHOD_HANDLE` is
no longer carried raw: it now resolves through the method-handle table.

**The parameter row was a level short.** `annotations_directory` read a
parameter row's `annotations_off` as an `annotation_set_item`; the format says
it names an `annotation_set_ref_list` -- one annotation SET per parameter -- so
the row is now resolved through that level and the answer is
`AnnotatedParameter { member_idx, parameter_sets }`. A control pins the
difference: pointing the row at zero is refused as
`AnnotationSetRefListOutOfBounds`, where the old reading would have answered
`AnnotationSetOutOfBounds`.

**`debug_info_item` has no length; it has a state machine.** The reader walks
the header (`line_start`, `parameters_size`, `uleb128p1` parameter names) and
then the bytecode to `DBG_END_SEQUENCE`, reading each operand at its own width
(ULEB128, SLEB128, `uleb128p1`), resolving every string and type index,
refusing the undefined opcodes 0x05..=0x08, and producing the positions the
special opcodes emit and the locals `DBG_START_LOCAL` introduces. A sequence
that stops before its terminator is `DebugInfoTruncated`, not a short listing --
and an operand that is present but encoded over-long is `BadUleb128`, because
there the bytes are there and the encoding is what is wrong (see the note at the
end of this section).

A sixth real fixture, `userprog/classes_map.dex` (748 bytes, sha256
`4a3641f01dfaeb4c709e6ceba52d57085f09695a49d0cd959e09f279d72d237f`), carries all
of it: `Lnyrqis/hello/Mapped;` with a static int `SEED` and one method
`run(int)V`, two method handles (a `STATIC_GET` of FIELD 0 and an `INVOKE_STATIC`
of METHOD 0), one call site `[handle 1, "handler", proto 0]`, a parameter row
through a one-entry ref list to a RUNTIME `Tag` annotation with `note = 5`, and
a `debug_info_item` stating line 1, the parameter `count`, positions (0,1) and
(1,2) and one local. Its map lists 20 entries in ascending offset order and is
compared entry by entry in the boot.

The minimal fixture was rebuilt to be conformant and reproducible: the
hand-built 128-byte `classes.dex` listed only its `map_list`, but the format
requires every DEX's map to describe its `header_item`, so the new map check
would have refused the repository's own smallest file. It is now written by
`tools/make_dex_fixture.py` as 140 bytes with `[header_item, map_list]` (and the
positive proof expects the two entries). The annotations fixture grew from 724
to 744 bytes for the parameter ref list and its item offsets moved; its earlier
transcript above is the run it was taken from.

Verified boot transcript (`tools/qemu_boot_test.sh`, passing, GATE_RC=0):

```
  experimental dex-demo: annotation tables -- a 744 byte DEX whose class Lnyrqis/hello/Annotated; names an annotations directory with 1 class, 1 field, 1 method and 1 parameter row(s): its class annotation is VISIBILITY_RUNTIME on Lnyrqis/hello/Marker; with elements count=7 (an int), name="hello" (a string) and flags=[true, false, ["hello"]] (nested arrays two deep), its field/method/parameter rows resolve to a BUILD item, a SYSTEM item and a RUNTIME item on member 0, and its static_values encoded array reads SEED=1000 -- the two-byte VALUE_INT whose width field a reader that ignored it would have read as 232 -- then label="hello"; classes_fields.dex, whose class names no directory and no static values, answers None and an empty array -- OK
  experimental dex-demo: map sections -- a 748 byte DEX whose map lists 20 entries in ascending offset order (header, the six header-declared tables, call_site_ids at 0xf4, method_handles at 0xf8, string_data, type_list, class_data, code_item, debug_info at 0x1b0, encoded_array, annotation_set_ref_list at 0x1c8, annotation_set, annotation_item, annotations_directory, map): handle 0 is a STATIC_GET of field 0 and handle 1 an INVOKE_STATIC of method 0, call site 0 reads the three-element array [handle 1, "handler", proto 0], the parameter row resolves through one ref-list entry to a RUNTIME Lnyrqis/hello/Tag; with note=5, and the debug_info item states line 1, one parameter "count", positions (0,1) and (1,2), and one local -- the class fixture's six-entry map reads back too -- OK
  experimental dex-demo: map refusals -- a map item type 0x0009 refused UnknownMapItemType, an entry stepping back to offset 0 refused MapNotSorted, a method_handles section claiming 8192 items past EOF refused MapSectionOutOfBounds, a string_ids count of 12 against the header's 13 refused MapSectionMismatch, a header_item of two items refused MapSectionMismatch, a map_list entry of size 2 refused MapSectionMismatch, a map whose entry is retagged so it no longer describes itself refused NoMapListEntry, a file with NO method_handles asked for one refused MethodHandleSectionMissing, handle index 2 past a two-handle section refused MethodHandleIndexOutOfBounds, handle type 0x09 refused BadMethodHandleType, a file with NO call_site_ids asked for one refused CallSiteSectionMissing, call-site index 1 refused CallSiteIndexOutOfBounds, a call site whose array holds two values refused BadCallSiteShape, a ref list past EOF refused AnnotationSetRefListOutOfBounds, one asked at zero refused AnnotationSetRefListOutOfBounds, one whose size overruns the file refused AnnotationSetRefListTruncated, one naming a set past EOF refused AnnotationSetOutOfBounds, a directory parameter row naming zero instead of a list refused AnnotationSetRefListOutOfBounds, a debug_info past EOF refused DebugInfoOutOfBounds, one asked at zero refused DebugInfoOutOfBounds, an undefined debug opcode 0x05 refused BadDebugInfoOpcode, a debug item cut before its parameter name refused DebugInfoTruncated, and one naming string 126 refused StringIndexOutOfBounds -- OK
  experimental dex-demo self-test: crypto known-answer OK, valid parse+integrity OK, bad-magic rejection OK, map-self-reference rejection OK, checksum rejection OK, class tables OK, table refusals OK, method tables OK, method refusals OK, field and code tables OK, member refusals OK, annotation tables OK, annotation refusals OK, map sections OK, map refusals OK -- OK
```

**What the fixture work caught before the boot.** The host harness
(`nykernel-package/target/dexhost`, gitignored, which `#[path]`-includes the real
`dex.rs` AND `dex_demo.rs` with only Adler-32/SHA-1 implemented locally) ran the
whole demo in seconds. Two mistakes were found there rather than in QEMU: the
map fixture's `debug_info_item` first sat at an unaligned offset, which failed
the generator's own 4-alignment assertion for every map entry (real DEX files
put debug info wherever it lands, but the fixture keeps sections word-aligned so
a control's patch is a word); and the minimal fixture's map had to grow its
`header_item` entry before the new check could accept it, which is the check
working rather than a workaround.

**Still absent, and named:** ART's private `hiddenapi_class_data_item` is
recognised as a known map entry and left unread -- its layout is outside the
public `dex-format` document, so guessing it would be worse than naming it; the
MEANING of the emitted positions and of annotations is still not checked; and
the string pool's de-duplication rules remain unread.

**A truncated `debug_info_item` and an over-long one are different answers.**
The item's operand readers mapped EVERY `read_uleb128`/`read_sleb128` failure
to `DebugInfoTruncated`, so five continuation bytes in a row -- bytes that are
present, but not a legal encoding -- were reported as a file that ended early.
The other LEB helpers in the module already split that case (`member_uleb`,
`annotation_uleb`): check that a byte is there, then let `BadUleb128` through.
`debug_uleb` now does exactly that, and the new `debug_sleb` does it for the
`DBG_ADVANCE_LINE` operand, which had the same collapse at its call site.

Two permanent boot controls pin the difference, five continuation bytes patched
into the fixture's real operands -- the parameter's `uleb128p1` name and the
`DBG_ADVANCE_LINE` delta -- each required to be refused as `BadUleb128`. Both
were `DebugInfoTruncated` before the change, so neither control can pass for the
truncation control's reason. No fixture byte changed; the two controls patch the
748-byte file at run time like every other map control.

Verified boot transcript (`tools/qemu_boot_test.sh`, passing, GATE_RC=0):

```
  experimental dex-demo: map refusals -- a map item type 0x0009 refused UnknownMapItemType, an entry stepping back to offset 0 refused MapNotSorted, a method_handles section claiming 8192 items past EOF refused MapSectionOutOfBounds, a string_ids count of 12 against the header's 13 refused MapSectionMismatch, a header_item of two items refused MapSectionMismatch, a map_list entry of size 2 refused MapSectionMismatch, a map whose entry is retagged so it no longer describes itself refused NoMapListEntry, a file with NO method_handles asked for one refused MethodHandleSectionMissing, handle index 2 past a two-handle section refused MethodHandleIndexOutOfBounds, handle type 0x09 refused BadMethodHandleType, a file with NO call_site_ids asked for one refused CallSiteSectionMissing, call-site index 1 refused CallSiteIndexOutOfBounds, a call site whose array holds two values refused BadCallSiteShape, a ref list past EOF refused AnnotationSetRefListOutOfBounds, one asked at zero refused AnnotationSetRefListOutOfBounds, one whose size overruns the file refused AnnotationSetRefListTruncated, one naming a set past EOF refused AnnotationSetOutOfBounds, a directory parameter row naming zero instead of a list refused AnnotationSetRefListOutOfBounds, a debug_info past EOF refused DebugInfoOutOfBounds, one asked at zero refused DebugInfoOutOfBounds, an undefined debug opcode 0x05 refused BadDebugInfoOpcode, a debug item cut before its parameter name refused DebugInfoTruncated, one whose parameter name is an over-long ULEB128 refused BadUleb128, one whose advance-line delta is an over-long SLEB128 refused BadUleb128, and one naming string 126 refused StringIndexOutOfBounds -- OK
  experimental dex-demo self-test: crypto known-answer OK, valid parse+integrity OK, bad-magic rejection OK, map-self-reference rejection OK, checksum rejection OK, class tables OK, table refusals OK, method tables OK, method refusals OK, field and code tables OK, member refusals OK, annotation tables OK, annotation refusals OK, map sections OK, map refusals OK -- OK
```
## The DEX member lists: a repeat is not a member (2026-11-28)

`class_data_item` states four counts and then four lists, and the format
requires each list sorted by member index with no duplicates. Every entry
carries an unsigned `field_idx_diff`/`method_idx_diff`, so no entry can name an
index BELOW its predecessor: a backwards step is not encodable at all. The one
way a list can break the rule is a delta of zero after the first entry, which
names the previous member again, and the reader now refuses that as
`ClassDataDuplicateMember`.

The check sits immediately after the delta is read and BEFORE the repeated
index is resolved, and that order is the finding: the index a repeat names is a
perfectly valid one that `field_id`/`method_id` will happily answer, so a reader
that validated the member first accepted the file. The first entry is exempt
because its delta is its absolute index, which may legally be zero -- which is
also why the fixture with a one-member method list still parses.

Two permanent boot controls pin the rule, both built by the fixture's own
`class_data` builder and spliced into its 22-byte span: a static list whose
second field delta is zero (index 0 twice) and a direct list whose third method
delta is zero (index 1 twice). Each repeated entry carries valid flags, a valid
`code_off` and a valid table index on its own, so only the delta is broken. No
fixture byte changed.

The host harness (`nykernel-package/target/dexhost`) found nothing to correct
this time; the boot gate below is the confirmation that the same two refusals
reach a real boot.

Verified boot transcript (`tools/qemu_boot_test.sh`, passing, GATE_RC=0):

```
  experimental dex-demo: member refusals -- a field index past field_ids refused FieldIndexOutOfBounds, a static delta landing past it refused FieldIndexOutOfBounds, a native method carrying a non-zero code_off refused MethodCodeMismatch before the offset was resolved, an ordinary method with none refused MethodCodeMismatch, an ABSTRACT method carrying one refused MethodCodeMismatch (while a native method without one is accepted OK), a code_off past EOF refused CodeItemOutOfBounds, a method delta landing past method_ids refused MethodIndexOutOfBounds, a static list that repeats its field refused ClassDataDuplicateMember and a direct list that repeats its method refused ClassDataDuplicateMember, a code_item whose words run past EOF refused CodeItemTruncated, one whose ins_size exceeds its registers refused CodeItemRegisterMismatch, an entry that stops before its code_off refused ClassDataTruncated, a class_data replacement one byte short, one byte long or an encoding grown past the span refused by the span rule, and code_item(0) refused CodeItemOutOfBounds -- OK
  experimental dex-demo self-test: crypto known-answer OK, valid parse+integrity OK, bad-magic rejection OK, map-self-reference rejection OK, checksum rejection OK, class tables OK, table refusals OK, method tables OK, method refusals OK, field and code tables OK, member refusals OK, annotation tables OK, annotation refusals OK, map sections OK, map refusals OK -- OK
```
## The DEX id tables are held to their ordering and uniqueness rules (2026-11-28)

The five id tables are not bags. `string_ids` must be sorted by UTF-16 code
point values with no duplicates, `type_ids` by string index, `proto_ids` by
(return type, then parameter list), `field_ids` by (class, name, type) and
`method_ids` by (class, name, proto). None of those is a property of any single
lookup, and the parser had never checked one. That mattered more than a missing
check usually does: the repository's own fixtures were not valid DEX files.
Every pool was in DECLARATION order -- "Lnyrqis/hello/Hello;" first and
"Hello.java" last -- and `classes_methods.dex` additionally numbered its class
type before its superclass, so its `type_ids` was out of order too. A reader
that resolves entries one at a time accepts all of that; the platform's
verifier does not.

`dex::table_order` now checks all five rules, and `parse` calls it, so no
resolver is ever handed a file whose tables cannot be valid. Sortedness is
decided by ADJACENT pairs -- a sequence is nondecreasing exactly when every
neighbour is -- and an equal pair is reported as the duplicate it is, so "out of
order" and "repeats an entry" stay different typed refusals:
`StringIdsNotSorted`/`StringIdsDuplicate`, `TypeIdsNotSorted`/`TypeIdsDuplicate`,
`ProtoIdsNotSorted`/`ProtoIdsDuplicate`, `FieldIdsNotSorted`/`FieldIdsDuplicate`
and `MethodIdsNotSorted`/`MethodIdsDuplicate`. Strings compare as MUTF-8 bytes,
which for well-formed MUTF-8 IS UTF-16 code unit order: the encoding has no
four-byte form, and both smaller forms order their leading bytes by value --
and `string` has already validated the encoding and the declared length before
the comparison uses it.

**The fixtures were canonicalized, not worked around.**
`tools/make_dex_fixture.py` now computes each pool's canonical order and the
type order it implies, and looks indices up by content (`H_S`, `M_S`, `F_S`,
`A_S`, `MP_S`) instead of writing numbers the format's rule would contradict.
Only the `string_ids` TABLE is permuted: the `string_data` blob keeps its
declaration order, so every offset the demo pins stayed exactly where it was and
all six files kept their sizes. What changed is what the format decides. In
`classes_fields.dex` the class descriptor is type 3 and `I` type 0; in
`classes_methods.dex` the class is type 1 because "Ljava/lang/Object;" sorts
before "Lnyrqis/hello/Hello;"; and `method_ids` sorts by name, so the fields
fixture's direct methods are make (0, native), run (1) and size (2) instead of
the order the file used to declare them. The demo and its controls follow the
files' own numbering, and the class_data builder's canonical call changed with
them.

Ten permanent boot controls, one per table per rule: a swapped adjacent pair
must be refused as `...NotSorted`, a copied entry as `...Duplicate`. Every entry
in all ten files still resolves -- the offsets, type indices, prototypes, field
and method records are all individually valid -- so a reader that answered
entries one at a time would accept all ten.

Verified boot transcript (`tools/qemu_boot_test.sh`, passing, GATE_RC=0):

```
  experimental dex-demo: table order -- all six fixtures satisfy the format's rules: classes_fields.dex's 12 strings are sorted by content and unique, its 5 types sorted by string index, its 2 protos by (return type, param list), its 3 fields by (class, name, type) and its 3 methods by (class, name, proto) -- classes_methods.dex shows the order is not declaration order, its class descriptor being type 1 because "Ljava/lang/Object;" sorts first -- and the minimal file's empty tables pass too -- OK
  experimental dex-demo: order refusals -- a swapped string pair refused StringIdsNotSorted, a duplicated string refused StringIdsDuplicate, a swapped type pair refused TypeIdsNotSorted, a duplicated type refused TypeIdsDuplicate, a swapped proto pair refused ProtoIdsNotSorted, a duplicated proto refused ProtoIdsDuplicate, a swapped field pair refused FieldIdsNotSorted, a duplicated field refused FieldIdsDuplicate, a swapped method pair refused MethodIdsNotSorted, and a duplicated method refused MethodIdsDuplicate -- OK
  experimental dex-demo self-test: crypto known-answer OK, valid parse+integrity OK, bad-magic rejection OK, map-self-reference rejection OK, checksum rejection OK, class tables OK, table refusals OK, table order OK, order refusals OK, method tables OK, method refusals OK, field and code tables OK, member refusals OK, annotation tables OK, annotation refusals OK, map sections OK, map refusals OK -- OK
```

The regenerated fixtures, same sizes, new digests: `classes_hello.dex` 294
bytes `1b1c2acf331ca7b688912b51e32fd366a5f93e917e2077553b62fa70917bf439`,
`classes_methods.dex` 436 `29841a535c4d9d7a7877dc4e1b1b49aa5a2bc1d60cd4af0d1f4bdc1a9bab6491`,
`classes_fields.dex` 604 `f383404edfffd3d8ab44c2a61a1a7a9cfbd7b440f5899dccb44ba75713340def`,
`classes_annotations.dex` 744 `13dd4b450d6902c4af7614a5dbd8de6abaefbf0abbf182e3174dfde8274c8cf9`,
`classes_map.dex` 748 `efcb355a44b1ceffe53e928dfc724d096f5ec013460b199b0ce552797886b073`;
the minimal `classes.dex` is byte-identical to before, since it has no ids to
order. Still absent and named: the string pool's de-duplication ACROSS different
files (this is one file's own pool), ART's `hiddenapi_class_data_item`, and the
meaning of positions and annotations.
