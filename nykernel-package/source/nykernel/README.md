# Nyrqis Kernel

A real, boot-verified x86_64 kernel for the Nyrqis platform (hybrid
microkernel per ADR-0006), built in phases against the repo's own
governance rules (NPC-001 §3.1/§6 -- see `engineering/RFC-0001-nykernel-bringup-plan.md`
at the repo root). This file is the entry point; it tells you what
this actually is and how to try it. For the full evidence -- every
boot transcript, every bug found and fixed, every negative control --
see `IMPLEMENTATION_STATUS.md` (this directory) and `PHASES.md` (repo
root).

## What this is, in one paragraph

A Multiboot2-bootable x86_64 kernel, written in Rust (`no_std`) plus a
small hand-written assembly boot stub, that boots to a running,
interrupt-driven, memory-managed state (Phases 1-2, grounded in the
already-`Accepted` NPS-001/NPS-017), then demonstrates -- as explicitly
labeled, pre-normative proofs of concept, not NPS-002/NPS-003
conformance -- cooperative multitasking, inter-task IPC, real ring-3
(user-mode) execution with genuine ELF loading (both fixed-address and
position-independent), per-process address-space isolation integrated
into the scheduler, and real memory reclamation. On top of those
prerequisites, it also now loads and executes a real, genuine Windows
PE32+ executable's own code in ring 3 -- the first actual Phase 5
capability, not just another prerequisite -- and parses and validates
a real, checksum-correct Android DEX file's container format, the
first actual Phase 6 capability. Every claim below is backed by an
actual QEMU boot transcript, not a design document.

## Try it

On a fresh Debian/Ubuntu host, `../../setup.sh` (run from this
directory) installs every dependency -- the `apt` packages, plus
`rustup` and the `rust-src` component -- and then runs both commands
below. It is idempotent and takes `SUDO_PASSWORD` for non-interactive
`sudo`.

```bash
./build.sh                    # build the kernel + all test userprograms
./tools/qemu_boot_test.sh     # automated boot test, real pass/fail exit code

# or, to watch it boot interactively:
./build.sh
mkdir -p target/isofiles/boot/grub && cp target/kernel.elf target/isofiles/boot/
cat > target/isofiles/boot/grub/grub.cfg <<'EOF'
set timeout=0
menuentry "Nyrqis Kernel" { multiboot2 /boot/kernel.elf; boot }
EOF
grub-mkrescue -o target/nyrqis.iso target/isofiles
qemu-system-x86_64 -cdrom target/nyrqis.iso -serial stdio -display none
```

Requires: `rustc`/`cargo` (current stable -- see "Toolchain notes" in
`IMPLEMENTATION_STATUS.md`), the `rust-src` component (`rustup component
add rust-src`), GNU `as`/`ld`, a `rust-lld` (it ships inside the rustup
toolchain; `build.sh` locates it automatically), `grub-mkrescue`/`xorriso`,
and `qemu-system-x86_64`. On Ubuntu the last three are `apt` packages;
`build.sh` and `tools/qemu_boot_test.sh` are self-contained otherwise.

## What's proven, at a glance

| Capability | Where | Status |
|---|---|---|
| Multiboot2 boot, long mode, GDT/IDT, exception handling | `boot/boot.s`, `src/gdt.rs`, `src/idt.rs` | ✅ NPS-001-grounded |
| Physical frame allocation + dynamic paging + kernel heap | `src/memory/` | ✅ NPS-001-grounded |
| PIC remap + real hardware timer interrupts | `src/pic.rs` | ✅ NPS-001-grounded |
| RTC periodic interrupt (second IRQ line, slave-PIC cascade) | `src/rtc.rs` | ✅ NPS-001-grounded |
| NX (no-execute) enforcement, W^X | `src/memory/paging.rs`, `src/experimental/elf.rs` | ✅ verified with a positive control |
| Cooperative kernel-mode task scheduling | `src/experimental/scheduler.rs` | 🔶 pre-normative (NPS-002 Draft) |
| Inter-task IPC (mailboxes) | `src/experimental/ipc.rs` | 🔶 pre-normative (NPS-003 Draft) |
| Ring-3 (user-mode) execution | `src/experimental/usermode.rs` | 🔶 pre-normative |
| Minimal ELF64 loader (`ET_EXEC`) | `src/experimental/elf.rs` | 🔶 pre-normative |
| Position-independent (`ET_DYN`) loading + relocations | `src/experimental/elf.rs`, `pie_demo.rs` | 🔶 pre-normative |
| Per-process address-space isolation | `src/experimental/address_space.rs`, `isolation_demo.rs` | 🔶 pre-normative |
| Address-space switching integrated into the scheduler | `src/experimental/isolated_tasks.rs` | 🔶 pre-normative |
| Real ring-3 task scheduling, 3 tasks (full-GPR trap handler) | `src/experimental/ring3_scheduler.rs` | 🔶 pre-normative |
| Address-space teardown + frame reclamation | `src/experimental/address_space.rs`, `teardown_demo.rs` | 🔶 pre-normative |
| TLB invalidation (`invlpg`) on unmap | `src/memory/paging.rs`, `tlb_demo.rs` | ✅ NPS-001-grounded |
| Guard pages for stack-overflow detection | `src/memory/guard_demo.rs` | ✅ NPS-001-grounded |
| ACPI RSDP validation + reclaimable-memory release | `src/acpi.rs`, `src/memory/frame_allocator.rs` | ✅ NPS-001-grounded |
| Minimal PE32+ (Windows) loader | `src/experimental/pe.rs`, `pe_demo.rs` | 🔷 first real Phase 5 capability |
| Minimal DEX (Android) parser + checksum/SHA-1 verify | `src/experimental/dex.rs`, `crypto.rs`, `dex_demo.rs` | 🔷 first real Phase 6 capability |

🔶 **"Pre-normative" is a real, repo-enforced distinction, not hedging.**
NPS-002 (Process/Thread Model) and NPS-003 (IPC/Capability Passing) are
still `Draft` per `NPC-004 Specification Index`; per `NPC-001 §5`, a
Draft spec isn't binding. Every 🔶 item above lives in
`src/experimental/` specifically so that's visible in the source tree,
with an explicit banner in `src/experimental/mod.rs`. None of it should
be read as "the kernel's real process model" -- it's a technology
proof for the mechanical primitives NPS-002/003 will eventually need
underneath their actual policy.

🔷 **Marks the one item that's different in kind, not just degree.**
`experimental::pe` isn't a mechanical prerequisite for Phase 5 -- it's
an actual first piece of Phase 5 itself (loading Windows' own
executable format). It's still not Windows *application* compatibility
(see "What's honestly still missing" below), but it's a genuinely
different category of progress than everything else in this table.

## How it's organized

```
boot/boot.s          Multiboot2 header, 32->64-bit transition, identity paging
linker.ld             Load-address layout, kernel image symbols
src/
  lib.rs              kernel_main -- the boot sequence, in order
  gdt.rs, idt.rs       GDT/TSS, IDT, CPU exception handlers
  acpi.rs              RSDP validation, RSDT/XSDT enumeration (reclaim support)
  pic.rs               8259 PIC remap, timer interrupt
  rtc.rs               CMOS/RTC periodic interrupt (IRQ8)
  multiboot.rs         Multiboot2 info-structure parsing
  memory/
    frame_allocator.rs Physical frames -- allocate + free-list deallocate
    paging.rs           Dynamic page mapping, NX enable
    heap.rs             Kernel heap (#[global_allocator])
    tlb_demo.rs         TLB-invalidation-on-unmap self-test
    guard_demo.rs       Guard-page stack-overflow detection + proof
  experimental/         Everything NOT NPS-001/017-grounded -- see its
                         own mod.rs banner before assuming any of it is
                         spec-conformant
tools/qemu_boot_test.sh Automated boot test (real pass/fail exit code)
userprog/                Real, separately-built test executables the
                          kernel loads and runs (not synthetic bytes)
```

## Every claim here is boot-verified, not asserted

Every capability in the table above has an actual transcript in
`IMPLEMENTATION_STATUS.md` from a real `qemu_boot_test.sh` run, most
with an accompanying **negative control** -- a temporary, deliberately
broken build that confirms the check would actually catch the failure
it claims to catch, reverted immediately after. Several negative
controls this session produced results *different* from what was
predicted (failing louder or earlier than expected) -- documented as
they actually happened, not adjusted to match the prediction. Real
bugs found along the way are documented with root cause, not just
"fixed" -- see IMPLEMENTATION_STATUS.md's "Bugs found and fixed"
sections throughout.

## What's honestly still missing

Read `IMPLEMENTATION_STATUS.md`'s "What does NOT exist yet" section in
full before assuming this kernel does something it doesn't. Headlines:
real dynamic linking (symbol-resolved relocations), preemptive ring-3
scheduling (still cooperative-only), guard pages wired under every live
kernel stack (the mechanism exists and is proven; it guards a dedicated
probe stack today, not the per-task kernel stacks), and -- the largest
gap by far --
everything an actual Windows or Android compatibility layer needs on
top of these kernel-level prerequisites: import handling for real
Windows binaries, a DEX/ART-equivalent runtime that
actually executes bytecode, the actual API surfaces. See `PHASES.md`
(repo root) Phase 5/6 for that scope, stated precisely.

## Governance

This kernel is built against `NPS-001` and `NPS-017` (both `Accepted`)
and the language rule in `ADR-0020` (Rust/C++/C for platform-critical
paths -- this kernel is 100% Rust + hand-written assembly, no Python
anywhere). See `engineering/RFC-0001-nykernel-bringup-plan.md` (repo
root) for the full alignment check, including what remains an honestly
open action item (NPS-007's exact text on the Windows kernel-mode
exclusion boundary, not yet retrievable in this environment).
