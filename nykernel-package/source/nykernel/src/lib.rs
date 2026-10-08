//! Nyrqis kernel -- Phase 1 (boot) + Phase 2 (memory management).
//!
//! Governed by NPS-001 (Kernel Architecture and Boot, Accepted v1.2.0)
//! and NPS-017 (NyHAL, Accepted); kernel type per ADR-0006 (hybrid
//! microkernel, Accepted); implementation language per ADR-0020
//! (Rust/C++/C for platform-critical paths, Accepted -- Python is
//! explicitly barred here, unlike the Linux backend PoC, because this
//! *is* the platform-critical path ADR-0020 was written about).
//!
//! Scope, honestly: Phase 1 (boot to a running, exception-handling
//! kernel) and Phase 2 (physical frame allocation, dynamic page
//! mapping, a working kernel heap) are complete. It does NOT implement
//! process isolation, scheduling, or IPC; those map to NPS-002 and
//! NPS-003, both still `Draft` (benchmark-blocked / transport-pending)
//! per NPC-004, so building against them as if they were binding would
//! violate NPC-001 §5. See IMPLEMENTATION_STATUS.md and PHASES.md for
//! full, evidence-based status.

#![no_std]
#![feature(abi_x86_interrupt)]
// `naked_functions` no longer needs a feature gate on current stable
// rustc (stable since 1.88); the `#[naked]` attribute is now
// `#[unsafe(naked)]` and naked bodies use `naked_asm!`.

extern crate alloc;

use alloc::vec::Vec;
use core::panic::PanicInfo;
use core::sync::atomic::{AtomicU32, Ordering};

pub mod acpi;
pub mod boot_phase;
pub mod experimental;
pub mod gdt;
pub mod idt;
pub mod memory;
pub mod multiboot;
pub mod pic;
pub mod rtc;
pub mod serial;
#[cfg(feature = "qemu_test")]
pub mod qemu_exit;

use boot_phase::{advance, BootPhase};
use memory::frame_allocator::BumpFrameAllocator;
use multiboot::MultibootInfo;

/// Called from boot.s's `long_mode_start`, once long mode is entered,
/// the temporary GDT is loaded, and the stack is set up. `rdi` holds
/// the multiboot magic (already validated in boot.s), `rsi` the
/// multiboot info structure's physical address -- now actually
/// consumed, by `multiboot::MultibootInfo` and the frame allocator it
/// feeds.
#[no_mangle]
pub extern "C" fn kernel_main(_multiboot_magic: u64, multiboot_info_addr: u64) -> ! {
    advance(BootPhase::LongModeEntry);

    gdt::init();
    advance(BootPhase::GdtInit);

    idt::init();
    advance(BootPhase::IdtInit);

    serial::init();
    advance(BootPhase::DiagnosticsInit);

    serial_println!("nyrqis kernel: phase 1 bring-up");
    serial_println!("  gdt: loaded (kernel code segment + tss)");
    serial_println!("  idt: loaded (divide-error, invalid-opcode, gpf, page-fault, double-fault)");
    serial_println!("  double-fault ist stack: index {}", gdt::DOUBLE_FAULT_IST_INDEX);

    // ---- Phase 2: memory management ----
    let multiboot = unsafe { MultibootInfo::load(multiboot_info_addr) }
        .unwrap_or_else(|| panic!("no valid Multiboot2 info structure at {:#x}", multiboot_info_addr));

    let mut frame_allocator = BumpFrameAllocator::new(multiboot);
    let total_usable = frame_allocator.total_usable_frames();
    serial_println!(
        "  multiboot: memory map parsed, {} usable 4KiB frames ({} MiB)",
        total_usable,
        total_usable * 4 / 1024
    );

    // ---- ACPI: validate the RSDP, then reclaim reclaimable memory ----
    // Firmware marks the pages holding ACPI tables as `AcpiReclaimable`:
    // usable, but only once the OS has read what it needs. We reach the
    // tables via the RSDP the loader handed off, validate them, and
    // only then let the frame allocator at those regions -- so the
    // default posture is "refuse", not "assume free".
    let acpi_info = acpi::init(multiboot.find_acpi_rsdp());
    if acpi_info.is_some() {
        let mut sample_frame: Option<u64> = None;
        multiboot.for_each_memory_region(|region| {
            if sample_frame.is_none()
                && region.region_type == multiboot::MemoryRegionType::AcpiReclaimable
                && region.length >= 4096
            {
                sample_frame = Some(region.base_addr / 4096);
            }
        });
        match sample_frame {
            Some(frame_no) => {
                let before = frame_allocator.frame_is_usable(frame_no);
                let regions = frame_allocator.reclaim_acpi();
                let after = frame_allocator.frame_is_usable(frame_no);
                serial_println!(
                    "  acpi reclaim: representative frame {:#x} usable before={} (expected false), {} reclaimable region(s) released, usable after={} (expected true) -- {}",
                    frame_no * 4096,
                    before,
                    regions,
                    after,
                    if !before && after { "OK" } else { "FAILED" }
                );
                assert!(
                    !before && after,
                    "ACPI-reclaimable memory was not correctly released for reuse"
                );
            }
            // Some firmware (every QEMU machine type this kernel has
            // been booted on) reports no `AcpiReclaimable` region at
            // all, so a real release can't be exercised here. Prove the
            // allocator's release contract with a clearly-labelled
            // synthetic control instead: take a currently-unusable
            // reserved range, show it flips from refused to usable when
            // released, then put it back so nothing real is handed out.
            None => {
                let mut reserved: Option<(u64, u64)> = None;
                multiboot.for_each_memory_region(|region| {
                    if reserved.is_none()
                        && region.region_type == multiboot::MemoryRegionType::Reserved
                        && region.length >= 4096
                    {
                        reserved = Some((region.base_addr, region.length));
                    }
                });
                let (base, length) = reserved.expect("no reserved region to use as a reclaim control");
                let frame_no = base / 4096;
                let before = frame_allocator.frame_is_usable(frame_no);
                frame_allocator.add_reclaimable_range(base, length);
                let after = frame_allocator.frame_is_usable(frame_no);
                frame_allocator.remove_reclaimable_range(base);
                let restored = frame_allocator.frame_is_usable(frame_no);
                serial_println!(
                    "  acpi reclaim: platform reports no AcpiReclaimable region; synthetic control on reserved {:#x}..{:#x}: usable before={} (expected false), after release={} (expected true), after restoring={} (expected false) -- {}",
                    base,
                    base + length,
                    before,
                    after,
                    restored,
                    if !before && after && !restored { "OK" } else { "FAILED" }
                );
                assert!(
                    !before && after && !restored,
                    "ACPI-reclaimable release behaviour is wrong (synthetic control)"
                );
            }
        }
    }

    let mut mapper = unsafe { memory::paging::init() };
    unsafe { memory::paging::enable_nx() };
    serial_println!("  paging: dynamic mapper live (CR3 read back, OffsetPageTable constructed), NX enabled (EFER.NXE)");

    unsafe { memory::heap::init(&mut mapper, &mut frame_allocator) }
        .unwrap_or_else(|e| panic!("heap init failed: {:?}", e));
    serial_println!(
        "  heap: mapped {} pages ({} KiB) at {:#x}, global allocator live",
        memory::heap::HEAP_SIZE_PAGES,
        memory::heap::HEAP_SIZE_BYTES / 1024,
        memory::heap::HEAP_START
    );

    // Real correctness proof, not just "it compiled": allocate on the
    // heap we just mapped -- outside Phase 1's static identity range,
    // see memory/heap.rs -- and verify the result. If dynamic paging
    // or the allocator were subtly broken, this either panics/faults
    // here or produces the wrong sum, not a silent lie in a log line.
    let v: Vec<u32> = (1..=100).collect();
    let sum: u32 = v.iter().sum();
    assert_eq!(sum, 5050, "heap-backed Vec<u32> sum mismatch -- Phase 2 memory management is broken");
    serial_println!(
        "  heap self-test: Vec<u32> of {} elements allocated and summed correctly ({})",
        v.len(),
        sum
    );
    drop(v);

    serial_println!("  frames allocated so far: {}", frame_allocator.frames_allocated());

    // TLB invalidation on unmap (IMPLEMENTATION_STATUS.md gap closure):
    // map -> unmap (`invlpg`) -> remap the same virtual address, and
    // confirm the remap reads back the new frame's bytes. NPS-001
    // territory (generic paging), so it runs here in Phase 2 rather
    // than in the pre-normative demo chain.
    let tlb_ok = memory::tlb_demo::run(&mut mapper, &mut frame_allocator);
    assert!(tlb_ok, "TLB-invalidation-on-unmap self-test failed");

    advance(BootPhase::MemoryInit);

    // ---- Interrupt controller: PIC remap + timer IRQ ----
    pic::init();
    advance(BootPhase::InterruptControllerInit);
    serial_println!("  pic: remapped (IRQ0-7 -> 0x20-0x27, IRQ8-15 -> 0x28-0x2F), timer unmasked");

    // Program the RTC's periodic interrupt and unmask it (IRQ8) plus
    // the master-side cascade line (IRQ2) its signal needs to reach the
    // CPU. The handler was already installed by `idt::init` above, so
    // there is no window where IRQ8 could fire with nothing to handle
    // it -- the same ordering the timer uses.
    unsafe { rtc::init() };
    pic::unmask(rtc::IRQ_CASCADE);
    pic::unmask(rtc::IRQ_RTC);
    serial_println!("  rtc: periodic interrupt armed at 1024 Hz, IRQ8 + cascade IRQ2 unmasked");

    x86_64::instructions::interrupts::enable(); // first `sti` anywhere in this kernel
    serial_println!("  interrupts: enabled (sti)");

    // Real correctness proof, not just "the PIC calls didn't fault":
    // busy-wait until the timer handler has actually incremented the
    // tick counter some meaningful number of times. If remapping were
    // wrong, or the handler weren't wired to the vector the PIC
    // actually uses, or EOI weren't sent (masking all further timer
    // interrupts after the first), this either hangs here forever or
    // never gets past a tick count of 1 -- not a silent false pass.
    const TARGET_TICKS: u64 = 20;
    while idt::timer_ticks() < TARGET_TICKS {
        x86_64::instructions::hlt(); // sleep between ticks instead of busy-spinning
    }
    serial_println!(
        "  timer self-test: {} real hardware timer interrupts observed and handled",
        idt::timer_ticks()
    );

    // Second real IRQ line: same evidence standard as the timer -- wait
    // until the RTC periodic handler has actually fired several times.
    // If the RTC weren't programmed correctly, or IRQ8/IRQ2 weren't
    // unmasked, or the handler didn't read register C (leaving the RTC's
    // line asserted, so it never fires again), this either hangs here
    // forever or never gets past a count of 1 -- not a silent false pass.
    // Exercises the slave-PIC cascade path the timer never touches.
    // Measured as a *delta*: the RTC started ticking as soon as it was
    // armed, so a bare absolute count would not prove interrupts are
    // arriving now, only that they arrived at some point. Waiting for
    // N more after a snapshot does.
    let rtc_before = rtc::ticks();
    const TARGET_RTC_TICKS: u64 = 4;
    while rtc::ticks() < rtc_before + TARGET_RTC_TICKS {
        x86_64::instructions::hlt();
    }
    serial_println!(
        "  rtc self-test: {} additional RTC periodic interrupts observed and handled after a snapshot ({} total; IRQ8, via the slave-PIC cascade)",
        rtc::ticks() - rtc_before,
        rtc::ticks()
    );

    advance(BootPhase::Ready);
    serial_println!("nyrqis kernel: phase 1+2+3-prep complete, boot phase = {} (Ready)", boot_phase::current());

    // ---- experimental: cooperative scheduler proof of concept ----
    // NOT NPS-002 conformance -- see crate::experimental's doc comment.
    // Real correctness proof, same standard as everything above: two
    // tasks each increment their own counter and print once per
    // activation, then yield; kernel_main (task 0) drives exactly
    // TARGET_ROUNDS round trips through the whole task set. With
    // strict round-robin over 3 tasks (main, A, B), N calls from main
    // correspond to exactly N full rounds, so both counters must equal
    // TARGET_ROUNDS afterward -- any scheduling bug (wrong next-task
    // index, corrupted saved register state, wrong stack layout) shows
    // up as a wrong count or a hang/fault, not a silent false pass.
    experimental::scheduler::init();
    experimental::scheduler::spawn(experimental_task_a);
    experimental::scheduler::spawn(experimental_task_b);
    serial_println!(
        "  experimental scheduler: {} tasks registered (pre-normative, not NPS-002 conformant)",
        experimental::scheduler::task_count()
    );
    experimental::ipc::init(experimental::scheduler::task_count());
    serial_println!("  experimental ipc: {} mailboxes initialized (pre-normative, not NPS-003 conformant)", experimental::scheduler::task_count());

    const TARGET_ROUNDS: u32 = 6;
    for _ in 0..TARGET_ROUNDS {
        experimental::scheduler::yield_task();
    }
    let a_runs = TASK_A_RUNS.load(Ordering::Relaxed);
    let b_runs = TASK_B_RUNS.load(Ordering::Relaxed);
    assert_eq!(a_runs, TARGET_ROUNDS, "task A ran the wrong number of times -- scheduler is broken");
    assert_eq!(b_runs, TARGET_ROUNDS, "task B ran the wrong number of times -- scheduler is broken");
    serial_println!(
        "  experimental scheduler self-test: {} round trips completed, task A ran {} times, task B ran {} times",
        TARGET_ROUNDS, a_runs, b_runs
    );

    let ipc_received = IPC_RECEIVED.load(Ordering::Relaxed);
    let ipc_mismatches = IPC_MISMATCHES.load(Ordering::Relaxed);
    assert_eq!(ipc_received, TARGET_ROUNDS, "task B didn't receive the expected number of IPC messages -- mailbox delivery is broken");
    assert_eq!(ipc_mismatches, 0, "task B received a message with the wrong sender/seq -- mailbox ordering or content is corrupted");
    serial_println!(
        "  experimental ipc self-test: {} messages sent task A -> task B and received correctly, 0 mismatches",
        ipc_received
    );

    // ---- experimental: isolated tasks under the ordinary scheduler ----
    // NOT NPS-002/NPS-003 conformance -- see crate::experimental's doc
    // comment. Extends the round-robin above from 3 tasks to 5: C and
    // D each get their own private address space (via
    // scheduler::spawn_with_cr3) while main/A/B continue sharing the
    // original one, all switching correctly together. Proves CR3
    // switching is correct under *repeated* back-and-forth (tens of
    // switches), not just the one-shot A->B->A sequence the isolation
    // demo below performs.
    let isolated_ok = experimental::isolated_tasks::run(&mut frame_allocator);
    assert!(isolated_ok, "isolated-task CR3 switching under repeated scheduling produced a corruption");

    // ---- experimental: DEX (Android) format parsing proof ----
    // NOT NPS-002/NPS-003 conformance -- see crate::experimental's doc
    // comment. The first real Phase 6 capability, same category as
    // pe_demo was for Phase 5: parses a real, checksum-correct DEX
    // file's container format (not bytecode execution -- see
    // experimental::dex's module doc for that honest scope boundary).
    // Needs no address space or ring-3 transition -- pure data
    // validation, so both positive and negative checks run here as
    // ordinary, permanent, every-boot code.
    let dex_ok = experimental::dex_demo::run();
    assert!(dex_ok, "DEX parsing proof failed -- either the valid file didn't parse, or a corrupted one wasn't rejected");

    // ---- experimental: address-space teardown proof ----
    // NOT NPS-002/NPS-003 conformance -- see crate::experimental's doc
    // comment. Every other demo mem::forgets its address spaces --
    // this proves the alternative actually works: build, use, destroy,
    // and confirm the freed physical frames genuinely come back for
    // reuse via the frame allocator's new free-list backing (see
    // memory::frame_allocator), not just that destroy() ran without
    // panicking.
    let teardown_ok = experimental::teardown_demo::run(&mut frame_allocator);
    assert!(teardown_ok, "address-space teardown did not fully reclaim its frames, or the reclaimed frame wasn't reused");

    // ---- experimental: real ring-3 task scheduling ----
    // NOT NPS-002/NPS-003 conformance -- see crate::experimental's doc
    // comment. The integration point flagged at the end of both
    // isolation_demo (ring-3 execution, hand-orchestrated) and
    // isolated_tasks (scheduler-integrated address spaces, ring-0
    // only): two real ring-3 tasks, suspended and resumed via a
    // hand-written full-GPR-save trap handler, driven through 20 real
    // yield traps. Chains into isolation_demo below with the same
    // frame allocator once its own self-test completes -- this is
    // kernel_main's last direct action; the rest of the demo chain
    // (ring3_scheduler's finale -> isolation_demo's own finale)
    // handles final reporting and halt/exit from here.
    unsafe { experimental::ring3_scheduler::start(frame_allocator) }
    // unreachable: start() is `-> !`.
}

static TASK_A_RUNS: AtomicU32 = AtomicU32::new(0);
static TASK_B_RUNS: AtomicU32 = AtomicU32::new(0);
static IPC_RECEIVED: AtomicU32 = AtomicU32::new(0);
static IPC_MISMATCHES: AtomicU32 = AtomicU32::new(0);

/// Task ids are assigned by `scheduler::spawn` in registration order:
/// 0 is always the caller of `scheduler::init()` (kernel_main itself),
/// so the first spawned task (A) is 1 and the second (B) is 2. Named
/// here rather than left as magic numbers at each call site.
const TASK_A_ID: usize = 1;
const TASK_B_ID: usize = 2;

extern "C" fn experimental_task_a() -> ! {
    loop {
        let seq = TASK_A_RUNS.fetch_add(1, Ordering::Relaxed) + 1;
        let msg = experimental::ipc::IpcMessage { sender: TASK_A_ID, seq };
        match experimental::ipc::send(TASK_B_ID, msg) {
            Ok(()) => serial_println!("    [experimental task A] activation {}: sent IPC message to task B", seq),
            Err(e) => serial_println!("    [experimental task A] activation {}: IPC SEND FAILED: {:?}", seq, e),
        }
        experimental::scheduler::yield_task();
    }
}

extern "C" fn experimental_task_b() -> ! {
    loop {
        let run = TASK_B_RUNS.fetch_add(1, Ordering::Relaxed) + 1;
        match experimental::ipc::try_receive(TASK_B_ID) {
            Some(msg) => {
                if msg.sender == TASK_A_ID && msg.seq == run {
                    IPC_RECEIVED.fetch_add(1, Ordering::Relaxed);
                    serial_println!(
                        "    [experimental task B] activation {}: received IPC message {{sender: {}, seq: {}}} -- OK",
                        run, msg.sender, msg.seq
                    );
                } else {
                    IPC_MISMATCHES.fetch_add(1, Ordering::Relaxed);
                    serial_println!(
                        "    [experimental task B] activation {}: received UNEXPECTED message {{sender: {}, seq: {}}}",
                        run, msg.sender, msg.seq
                    );
                }
            }
            None => {
                IPC_MISMATCHES.fetch_add(1, Ordering::Relaxed);
                serial_println!("    [experimental task B] activation {}: no message available (unexpected)", run);
            }
        }
        experimental::scheduler::yield_task();
    }
}

/// Halts the CPU in a loop rather than spinning it hot -- `hlt` puts
/// the core to sleep until the next interrupt, which matters even in
/// a single-core kernel because it's what a real idle task will
/// eventually do too; this is that behavior's first appearance, not a
/// throwaway.
pub fn hlt_loop() -> ! {
    loop {
        x86_64::instructions::hlt();
    }
}

#[panic_handler]
fn panic(info: &PanicInfo) -> ! {
    serial_println!("KERNEL PANIC: {}", info);
    hlt_loop();
}
