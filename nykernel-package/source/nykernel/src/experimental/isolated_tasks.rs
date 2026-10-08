//! Isolated tasks, driven by the ordinary cooperative scheduler.
//!
//! `experimental::isolation_demo` proved address-space isolation with
//! a single switch away and back (A -> B -> A). This module proves the
//! stronger property that actually matters for a scheduler: isolation
//! holds under *repeated* switching, driven through the same
//! `yield_task()` round-robin the Phase 3 demo uses -- extended from 3
//! tasks (main, A, B) to 5 (main, A, B, C, D), where C and D each have
//! their own private address space (via `spawn_with_cr3`) and A/B/main
//! continue sharing the original one, all switching correctly with
//! each other in the same round-robin.
//!
//! See `crate::experimental`'s banner -- not NPS-002 conformance.

use x86_64::structures::paging::PageTableFlags;
use x86_64::VirtAddr;

use crate::experimental::address_space::{self, AddressSpace};
use crate::experimental::scheduler;
use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::memory::paging;
use crate::serial_println;
use core::sync::atomic::{AtomicU32, Ordering};

/// Distinct from every other virtual address this kernel uses
/// (identity map, heap at `0x1...`, ring-3 demo at `0x2...`) so a
/// mistake that accidentally landed one of C/D's mappings on top of
/// something else would show up as a mapping failure, not a silent
/// collision.
const ISOLATED_TEST_ADDR: u64 = 0x_3000_0000_000;

const MAGIC_C: u64 = 0xCCCCCCCC;
const MAGIC_D: u64 = 0xDDDDDDDD;

/// How many activations each of C and D gets, driven by `main` calling
/// `yield_task()` `ROUNDS * 5` times total (5 tasks now in the
/// round-robin, so each full cycle touches every task once). More than
/// [`experimental::scheduler`]'s original 6, specifically to stress
/// *repeated* CR3 reloads rather than just proving one switch works.
const ROUNDS: u32 = 10;

static C_CHECKS_OK: AtomicU32 = AtomicU32::new(0);
static C_CHECKS_FAILED: AtomicU32 = AtomicU32::new(0);
static D_CHECKS_OK: AtomicU32 = AtomicU32::new(0);
static D_CHECKS_FAILED: AtomicU32 = AtomicU32::new(0);

/// Builds address spaces C and D (each with a private page at
/// [`ISOLATED_TEST_ADDR`]), spawns their tasks via
/// `scheduler::spawn_with_cr3`, drives `ROUNDS` full round-robin
/// cycles, and returns whether every check across both tasks passed.
/// Must run after `scheduler::init()`/the original A-B task spawns
/// (extends that same round-robin rather than starting a new one) and
/// after the kernel heap is live.
pub fn run(frame_allocator: &mut BumpFrameAllocator) -> bool {
    let original_cr3 = scheduler::current_cr3();

    let cr3_c = build_isolated_space(frame_allocator, "C");
    let cr3_d = build_isolated_space(frame_allocator, "D");

    // build_isolated_space leaves its own space active to do the
    // mapping; restore the original before spawning, since spawn_task
    // C/D's *first* activation (not this setup code) is what should
    // run under their own space, and main/A/B's shared space must be
    // active again before the round-robin resumes.
    restore_cr3(original_cr3);

    // Allocate C's and D's guarded kernel stacks through the currently
    // active (restored, shared) address space -- the region is shared,
    // so this is visible under C's and D's own private spaces too.
    let mut spawn_mapper = unsafe { address_space::active_mapper() };
    scheduler::spawn_with_cr3(task_c_entry, cr3_c, &mut spawn_mapper, frame_allocator);
    scheduler::spawn_with_cr3(task_d_entry, cr3_d, &mut spawn_mapper, frame_allocator);
    serial_println!(
        "  experimental isolated-tasks: {} total tasks in round-robin (main, A, B, C[own cr3 {:#x}], D[own cr3 {:#x}])",
        scheduler::task_count(), cr3_c, cr3_d
    );

    for _ in 0..(ROUNDS * scheduler::task_count() as u32) {
        scheduler::yield_task();
    }

    let c_ok = C_CHECKS_OK.load(Ordering::Relaxed);
    let c_failed = C_CHECKS_FAILED.load(Ordering::Relaxed);
    let d_ok = D_CHECKS_OK.load(Ordering::Relaxed);
    let d_failed = D_CHECKS_FAILED.load(Ordering::Relaxed);
    serial_println!(
        "  experimental isolated-tasks self-test: C {} ok / {} failed, D {} ok / {} failed (across {} switches each, repeated CR3 reload)",
        c_ok, c_failed, d_ok, d_failed, c_ok + c_failed
    );

    c_failed == 0 && d_failed == 0 && c_ok > 0 && d_ok > 0
}

fn build_isolated_space(frame_allocator: &mut BumpFrameAllocator, label: &str) -> u64 {
    let mut space = AddressSpace::new_sharing_kernel(frame_allocator)
        .unwrap_or_else(|_| panic!("failed to build isolated address space {}", label));
    unsafe { space.activate() };
    let mut mapper = space.mapper();
    let flags = PageTableFlags::PRESENT | PageTableFlags::WRITABLE | PageTableFlags::NO_EXECUTE; // kernel-mode task, no USER_ACCESSIBLE needed, never executed
    let mapped = paging::map_range(&mut mapper, frame_allocator, VirtAddr::new(ISOLATED_TEST_ADDR), 1, flags);
    assert_eq!(mapped, 1, "failed to map isolated test page for space {}", label);
    let phys = address_space::pml4_phys_addr(&space).as_u64();
    // Leak `space` deliberately: its PML4 must outlive this function
    // (the task using it runs for the rest of the kernel's life), and
    // this proof of concept has no address-space teardown story yet
    // (matching the frame allocator's own no-`dealloc` honesty).
    core::mem::forget(space);
    phys
}

fn restore_cr3(cr3_phys_addr: u64) {
    use x86_64::registers::control::{Cr3, Cr3Flags};
    use x86_64::structures::paging::PhysFrame;
    use x86_64::PhysAddr;
    let frame = PhysFrame::containing_address(PhysAddr::new(cr3_phys_addr));
    unsafe { Cr3::write(frame, Cr3Flags::empty()) };
}

extern "C" fn task_c_entry() -> ! {
    let mut first = true;
    loop {
        let ptr = ISOLATED_TEST_ADDR as *mut u64;
        if first {
            unsafe { core::ptr::write_volatile(ptr, MAGIC_C) };
            first = false;
        }
        let observed = unsafe { core::ptr::read_volatile(ptr) };
        if observed == MAGIC_C {
            C_CHECKS_OK.fetch_add(1, Ordering::Relaxed);
        } else {
            C_CHECKS_FAILED.fetch_add(1, Ordering::Relaxed);
            serial_println!("    [isolated task C] CORRUPTED: read {:#x}, expected {:#x}", observed, MAGIC_C);
        }
        scheduler::yield_task();
    }
}

extern "C" fn task_d_entry() -> ! {
    let mut first = true;
    loop {
        let ptr = ISOLATED_TEST_ADDR as *mut u64;
        if first {
            unsafe { core::ptr::write_volatile(ptr, MAGIC_D) };
            first = false;
        }
        let observed = unsafe { core::ptr::read_volatile(ptr) };
        if observed == MAGIC_D {
            D_CHECKS_OK.fetch_add(1, Ordering::Relaxed);
        } else {
            D_CHECKS_FAILED.fetch_add(1, Ordering::Relaxed);
            serial_println!("    [isolated task D] CORRUPTED: read {:#x}, expected {:#x}", observed, MAGIC_D);
        }
        scheduler::yield_task();
    }
}
