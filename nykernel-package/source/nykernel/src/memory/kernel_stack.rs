//! Guarded kernel stacks, allocated from one region shared by every
//! address space.
//!
//! `memory::guard_demo` proves the guard-page *mechanism* (a page left
//! unmapped directly below a stack so an overflow faults instead of
//! corrupting whatever sits below). This module is what wires that
//! mechanism under the kernel stacks a live ring-3 task actually
//! traps onto -- each `experimental::address_space::AddressSpace`'s
//! private RSP0 stack -- rather than only under a dedicated probe.
//!
//! # Why one shared region, and why it lives inside PML4 index 2
//!
//! A cooperative context switch changes `CR3` *before* it changes
//! `RSP` (see `experimental::ring3_scheduler`'s `ring3_yield_trap_entry`
//! and `experimental::scheduler`'s `switch_to`): the CPU keeps
//! executing on the outgoing task's stack for a few instructions after
//! the new address space is already active, so the outgoing stack must
//! stay mapped under the *incoming* space's page tables. That is why
//! kernel stacks used to be plain heap allocations -- the kernel heap
//! lives in PML4 index 2, which `SHARED_PML4_INDICES` copies into
//! every space, so a heap-backed stack is visible no matter which
//! process's tables are active.
//!
//! Guard pages cannot live *in* the heap, though: unmapping a page the
//! heap allocator still owns would corrupt it (the allocator would
//! happily hand that page out again). So this module carves a dedicated
//! region *inside the same shared PML4 index* -- address
//! `0x140_0000_0000`, far above the heap's own 1 MiB at
//! `0x100_0000_0000` -- where it can map stacks and leave the page
//! directly below each one permanently unmapped. Because that region
//! sits under the shared index-2 subtree, every address space sees it
//! for free: the per-slot page-table entries this allocator creates are
//! written once into the shared PDPT/PD/PT chain that all spaces
//! already point at.
//!
//! The region is a monotonic bump allocator (no free) -- consistent
//! with this proof of concept's frame allocator and its documented
//! "no teardown story yet" honesty. It is large (hundreds of GiB of
//! address space) and only ever hosts a handful of stacks per boot.

use core::sync::atomic::{AtomicU64, Ordering};
use x86_64::structures::paging::{OffsetPageTable, PageTableFlags};
use x86_64::VirtAddr;

use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::memory::paging;

/// Base of the shared guarded-stack region. Chosen inside PML4 index 2
/// (`0x100_0000_0000 .. 0x180_0000_0000`, already shared by every
/// address space via `SHARED_PML4_INDICES`) but far from the heap's own
/// 1 MiB at `0x100_0000_0000`, so a stack can never collide with a real
/// allocation.
pub const REGION_BASE: u64 = 0x_140_0000_0000;

/// Usable size of a kernel stack by default, matching the 5 pages
/// `experimental::address_space` used before stacks moved here.
pub const DEFAULT_STACK_PAGES: u64 = 5;

/// Next free byte offset within the region. Monotonic, never reclaimed.
static NEXT_OFFSET: AtomicU64 = AtomicU64::new(0);

/// A built guarded stack. `guard` is the page directly below `base` and
/// is deliberately left unmapped; a downward-growing stack that
/// overflows runs straight into it.
#[derive(Clone, Copy, Debug)]
pub struct GuardedStack {
    /// Lowest *mapped* address of the stack (a page boundary).
    pub base: VirtAddr,
    /// The unmapped guard page directly below `base`.
    pub guard: VirtAddr,
    /// One past the highest mapped byte -- the correct initial stack
    /// pointer for a downward-growing stack.
    pub top: VirtAddr,
    pub pages: u64,
}

/// Maps `pages` fresh 4 KiB pages backed by newly allocated frames in
/// the shared region, leaving the page directly below them unmapped as
/// a guard. Returns `None` if any page could not be mapped (a partial
/// stack would be a misleading thing to hand back, so this is
/// all-or-nothing).
///
/// The caller may pass *any* space's mapper: because the region lives
/// under the shared index-2 subtree, the entries this writes are the
/// same ones every address space resolves through.
pub fn allocate(
    mapper: &mut OffsetPageTable<'static>,
    frame_allocator: &mut BumpFrameAllocator,
    pages: u64,
) -> Option<GuardedStack> {
    // One guard page plus `pages` usable pages per slot; the guard sits
    // at the slot base, the usable run immediately above it.
    let stride = (pages + 1) * 4096;
    let offset = NEXT_OFFSET.fetch_add(stride, Ordering::SeqCst);
    let slot_base = VirtAddr::new(REGION_BASE + offset);
    let base = slot_base + 4096u64;

    let flags = PageTableFlags::PRESENT | PageTableFlags::WRITABLE | PageTableFlags::NO_EXECUTE;
    if paging::map_range(mapper, frame_allocator, base, pages, flags) != pages {
        return None;
    }

    Some(GuardedStack {
        base,
        guard: slot_base,
        top: base + pages * 4096,
        pages,
    })
}

/// Checks a built stack's page-table state directly (not by inferring
/// from behavior): every stack page present, the guard page absent, and
/// the page just above the stack (the next slot's guard, also
/// permanently unmapped) absent -- so the guard cannot pass by the
/// stack merely never having been mapped. Returns `true` iff all of
/// that holds.
pub fn verify(mapper: &OffsetPageTable<'static>, stack: &GuardedStack) -> bool {
    if paging::translate(mapper, stack.guard).is_some() {
        return false;
    }
    if paging::translate(mapper, stack.top).is_some() {
        return false;
    }
    (0..stack.pages).all(|i| paging::translate(mapper, stack.base + i * 4096).is_some())
}
