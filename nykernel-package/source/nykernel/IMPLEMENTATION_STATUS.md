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
  toolchains). What still does not exist: Win32 API surface
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
