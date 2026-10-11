# Nyrqis Kernel -- Implementation Status

**Self-rating: Experimental. Phases 1-2 Complete and Verified against
NPS-001/017. Ten pre-normative Phase 5/6 kernel-level prerequisites
Complete and Verified (`src/experimental/mod.rs`'s banner -- none of
it is NPS-002/003 conformance). Two genuine first-capability
increments landed: a minimal PE32+ (Windows) loader, proven by
executing a real PE binary's own code in ring 3,and a minimal DEX (Android) format parser, proven against a real DEX
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
round-robined purely by the timer interrupt (IRQ0).**

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

## What does NOT exist yet (do not assume otherwise)

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
  to its periodic tick) is also not implemented -- that needs the CMOS
  time registers' BCD/binary and 12/24-hour modes, deliberately left
  out of the periodic-interrupt module
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
  not silently mishandled. `PT_GNU_RELRO`'s "make read-only again after
  relocating" step is also not implemented; a relocatable segment this
  loader maps writable stays writable
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
  rather than mishandled, but most real-world Windows binaries
  additionally need imports, so they still won't run. No
  Win32 API surface, no resources, no exception tables
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
- UEFI boot or Secure Boot (ADR-0014 ~~is still `Proposed`~~ was
  **Accepted 2026-10-11**, AG row I3 -- Phase 10 is now an open
  implementation phase, not a governance-blocked one) -- Phase 1
  still boots via legacy BIOS/GRUB2/Multiboot2 only

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
- *DEX bytecode execution (ART/Dalvik semantics)* -- parsing the DEX
  container (done) is not running the bytecode; the latter is
  comparable in scope to writing a JVM.
- *Real dynamic linking for ELF* (`R_X86_64_GLOB_DAT`,
  `_JUMP_SLOT`, symbol/string tables, cross-object resolution,
  `PT_GNU_RELRO`) -- a dynamic linker is its own subsystem.
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

**Current setup: rustup stable (rustc 1.97.1) + `rust-src`.** The build
needs the standard `rustc`/`cargo`, the `rust-src` component (so
`-Z build-std` can rebuild `core`/`alloc` for the freestanding target),
and a `rust-lld`. `../../setup.sh` installs all of that on a fresh
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

After the migration the kernel **builds clean (debug + release) and the
full `qemu_boot_test.sh` chain passes** -- every prior demo's self-test
is unchanged, so the migration introduced no behavioral regression. The
remaining compiler *warnings* (26 of them, mostly `static mut`
shared/mutable-reference lints new under Rust 2024's edition-lint set)
are pre-existing patterns, not errors, and are left as-is rather than
silently silenced.

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
