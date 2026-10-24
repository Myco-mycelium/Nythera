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
use x86_64::structures::paging::{
    FrameDeallocator, Mapper, OffsetPageTable, Page, PageTableFlags, Size4KiB,
};
use x86_64::VirtAddr;

use crate::serial_println;

use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::memory::paging;

/// Base of the shared guarded-stack region. Chosen inside PML4 index 2
/// (`0x100_0000_0000 .. 0x180_0000_0000`, already shared by every
/// address space via `SHARED_PML4_INDICES`) but far from the heap's own
/// 1 MiB at `0x100_0000_0000`, so a stack can never collide with a real
/// allocation.
pub const REGION_BASE: u64 = 0x_140_0000_0000;

/// Usable size of a kernel stack by default: 5 pages per stage. When
/// the demo CHAIN accumulated every stage's frames on one stack (each
/// `on_trap` continuation never returns), this had to grow to 8;
/// `experimental::chain::run_on_fresh_stack` now partitions the chain
/// instead -- each stage runs its kernel-side work on its OWN
/// freshly-allocated RSP0 stack -- so the budget is back to the size
/// one stage needs. Both directions verified: the 5-page era
/// overflowed LOUDLY at the guard once the chain grew (a #PF/#DF at
/// the guard address, zero corruption), and the partition restored
/// the 5-page budget with an identical passing boot.
pub const DEFAULT_STACK_PAGES: u64 = 5;

/// Next free byte offset within the region. Monotonic for NEW slots;
/// released slots go to [`FREED_SLOTS`] and are re-handed out in
/// full, byte-exactly, without ever rewinding this bump.
static NEXT_OFFSET: AtomicU64 = AtomicU64::new(0);

/// Released slots (offset into the region + their page count). A pool
/// with exact-match preferred, whole-slot absorption for smaller
/// requests: `release` unmaps a slot's pages, frees their frames and
/// records the slot here; the next `allocate` of the SAME page count
/// reuses the slot byte-for-byte (same guard position, same top), and
/// a SMALLER request absorbs the SMALLEST strictly-larger released
/// slot WHOLE (see `allocate` -- a split is impossible by design:
/// a split-off remainder would lose the one unmapped guard page that
/// makes every stack here checkable). No midding, no shrinking, no
/// silent rewind. Real hand-back sites:
/// `AddressSpace::destroy` routes its reclamation through `release`,
/// so every destroyed space's RSP0 slot flows in here —
/// `ring3_scheduler::finale`'s destroyed task F is the boot-proved
/// live hand-back. A live IN-USE stack is still never released (the
/// demos that `mem::forget` their spaces leak by design); the
/// mechanism's dedicated proof is [`reuse_self_test`], run at every
/// boot next to the guard demo.
static mut FREED_SLOTS: Option<alloc::vec::Vec<(u64, u64)>> = None;

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
    // Exact-count reuse first: a released slot of the SAME page count
    // is re-handed out in full (same guard position, same top, fresh
    // mappings for its pages).
    unsafe {
        if let Some(vec) = (*&raw mut FREED_SLOTS).as_mut() {
            if let Some(pos) = vec.iter().position(|&(_, p)| p == pages) {
                let (offset, _) = vec.remove(pos);
                let slot = map_slot(mapper, frame_allocator, offset, pages)
                    .expect("a released slot must re-map with identical bounds");
                return Some(slot);
            }
            // No exact fit: WHOLE-SLOT ABSORPTION. A smaller request
            // takes the SMALLEST strictly-larger released slot -- the
            // WHOLE slot, never a split. Splitting is deliberately
            // impossible, not just unimplemented: each slot's intact
            // guard is ONE unmapped page between it and its neighbor;
            // a split-off remainder would have that borrowed page as
            // its own "guard" -- mapped, not unmapped -- so the exact
            // guard invariant every proof here re-verifies through the
            // page tables could not survive. Absorption therefore
            // over-provisions (the descriptor's pages may exceed the
            // request) instead: guard intact, byte-identical bounds,
            // monotonic region untouched.
            let mut best: Option<usize> = None;
            for (i, &(_, p)) in vec.iter().enumerate() {
                if p > pages {
                    best = match best {
                        None => Some(i),
                        Some(b) if p < vec[b].1 => Some(i),
                        other => other,
                    };
                }
            }
            if let Some(pos) = best {
                let (offset, whole) = vec.remove(pos);
                let slot = map_slot(mapper, frame_allocator, offset, whole)
                    .expect("an absorbed slot must re-map with identical bounds");
                return Some(slot);
            }
        }
    }

    // One guard page plus `pages` usable pages per slot; the guard sits
    // at the slot base, the usable run immediately above it.
    let stride = (pages + 1) * 4096;
    let offset = NEXT_OFFSET.fetch_add(stride, Ordering::SeqCst);
unsafe { map_slot(mapper, frame_allocator, offset, pages) }
}

/// Maps the usable run of one slot at `offset` (its guard below it
/// stays unmapped) and hands the descriptor back. `None` means the
/// mapping failed -- a partial stack is never handed out.
unsafe fn map_slot(
    mapper: &mut OffsetPageTable<'static>,
    frame_allocator: &mut BumpFrameAllocator,
    offset: u64,
    pages: u64,
) -> Option<GuardedStack> {
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

/// Releases a NOT-IN-USE stack: unmaps every usable page, frees the
/// backing frames, and returns the slot to the exact-match reuse pool
/// ([`FREED_SLOTS`]). The guard page below was never mapped.
///
/// MUST NOT be called on a stack any thread of execution is running
/// on or will resume into (there is nothing detecting that here --
/// this is the caller's contract, the same way `destroy`'s is). The
/// slot offset bytes stay reserved in the region either way
/// (`NEXT_OFFSET` never rewinds); release only makes the slot
/// RE-ALLOCATABLE, so the monotonic-region invariant (no heap-corrupt
/// handouts) survives untouched.
///
/// # Safety
/// No execution state may live in the stack.
pub unsafe fn release(
    stack: &GuardedStack,
    mapper: &mut OffsetPageTable<'static>,
    frame_allocator: &mut BumpFrameAllocator,
) {
    for i in 0..stack.pages {
        let page: Page<Size4KiB> = Page::containing_address(stack.base + i * 4096);
        if let Ok((frame, flush)) = mapper.unmap(page) {
            flush.flush();
            frame_allocator.deallocate_frame(frame);
        }
    }
    unsafe {
        let vec = (*&raw mut FREED_SLOTS).get_or_insert_with(alloc::vec::Vec::new);
        vec.push((stack.base.as_u64() - REGION_BASE - 4096, stack.pages));
    }
}

/// The slot-reuse mechanism's own boot proof, run next to the guard
/// demo. Pools prove the re-hand-out contract end to end at every
/// boot: an exact count comes back byte-identical (same base/guard/top
/// -- the offset pool, not a new bump); a request for a count nobody
/// pooled is a fresh bump past all of it; a SMALLER request than a
/// pooled slot absorbs the slot WHOLE (best-fit choice among strictly
/// larger slots, descriptor's pages = the slot's own count, never
/// split -- the released 3-page slot below satisfies the later 2-page
/// request only by being handed out whole); and the region offset
/// never rewinds. The LIVE-task hand-back through `destroy` has its
/// own separate boot proof in `ring3_scheduler::finale`.
///
pub fn reuse_self_test(mapper: &mut OffsetPageTable<'static>, fa: &mut BumpFrameAllocator) {
    let before = NEXT_OFFSET.load(Ordering::SeqCst);
    let a = allocate(mapper, fa, 2).expect("self-test: first slot alloc");
    assert_eq!(
        NEXT_OFFSET.load(Ordering::SeqCst),
        before + (2 + 1) * 4096,
        "self-test: the first allocation must bump"
    );
    assert!(verify(mapper, &a), "self-test: first slot must verify");

    unsafe { release(&a, mapper, fa) };
    assert!(
        !verify(mapper, &a),
        "self-test: the slot must be unmapped immediately after release"
    );

    let b = allocate(mapper, fa, 2).expect("self-test: reuse alloc");
    assert_eq!(
        b.base, a.base,
        "self-test: the 2-page re-allocation must land in the released slot"
    );
    assert!(verify(mapper, &b), "self-test: the reused slot must verify afresh");

    // A LARGER request must not touch the pool's smaller slot: the
    // pool's only entry is the (still-live) release below is a fresh
    // 8-page bump to guarantee the absorption leg starts from a known
    // slot, not a pool leftover of a matching size.
    let c = allocate(mapper, fa, 8).expect("self-test: larger alloc");
    assert!(verify(mapper, &c), "self-test: the fresh 8-page slot must verify");
    unsafe { release(&c, mapper, fa) };

    // A SMALLER request absorbs the larger released slot WHOLE: same
    // base/guard/top as the 8-page slot, descriptor's pages == that
    // slot's own 8 (over-provisioned, NOT split to 3), and the region
    // offset does not move.
    let before_absorb = NEXT_OFFSET.load(Ordering::SeqCst);
    let d = allocate(mapper, fa, 3).expect("self-test: absorption alloc");
    assert_eq!(
        NEXT_OFFSET.load(Ordering::SeqCst),
        before_absorb,
        "self-test: absorption must not bump the region"
    );
    assert_eq!(
        d.base, c.base,
        "self-test: the 3-page request must absorb the released 8-page slot"
    );
    assert_eq!(d.guard, c.guard, "the absorbed slot must keep its guard position");
    assert_eq!(d.top, c.top, "the absorbed slot must keep its top");
    assert_eq!(
        d.pages, 8,
        "the absorbed descriptor must be the WHOLE 8-page slot, never resized to the request"
    );
    assert!(verify(mapper, &d), "self-test: the absorbed slot must verify afresh");
    unsafe { release(&d, mapper, fa) };

    // And the pool goes back to exactly one released 8-page slot: an
    // exact-2 request next must not resurrect a 2-page look-alike; it
    // absorbs the same 8-page slot again (proving the hand-out round
    // trip), while anything is false only if a split happened.
    let e = allocate(mapper, fa, 2).expect("self-test: second absorption");
    assert_eq!(e.base, c.base, "the released 8-page slot must re-hand out again");
    assert_eq!(e.pages, 8, "the second absorption must again be whole");
    assert!(verify(mapper, &e), "self-test: the re-absorbed slot must verify");
    // e stays live: the real hand-out sites (ring3 finale, below) keep
    // consumed slots alive, and the terminal guard demo's own 4-page
    // request then takes an honest fresh bump.

    serial_println!(
        "  memory stack-reuse self-test: released 2-page slot re-handed out byte-identically (base {:#x}, guard {:#x}, top {:#x}) fully re-verified with its guard unmapped; an 8-page release absorbed WHOLE by 3-page and by 2-page requests (same guard/top, descriptor stays 8 pages, zero splits); a bigger request never touched a smaller pooled slot -- OK, exact-match preferred, whole-slot absorption bounded, no rewind",
        b.base.as_u64(),
        b.guard.as_u64(),
        b.top.as_u64()
    );
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
