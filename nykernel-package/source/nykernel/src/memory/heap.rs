//! Kernel heap.
//!
//! Maps a fixed virtual region -- deliberately chosen well outside
//! Phase 1's static identity-mapped first 1GiB, so successfully
//! reading/writing through it is real evidence that dynamic paging
//! (paging.rs) works, not an accident of already-mapped memory -- and
//! hands it to `linked_list_allocator` as the `#[global_allocator]`.
//! From the moment `init()` returns successfully, `alloc::vec::Vec`,
//! `alloc::boxed::Box`, etc. are usable anywhere in the kernel.

use linked_list_allocator::LockedHeap;
use x86_64::structures::paging::{OffsetPageTable, PageTableFlags};
use x86_64::VirtAddr;

use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::memory::paging;

/// Arbitrary, well outside the first 1GiB (0x0..0x4000_0000) that
/// boot.s statically identity-mapped -- see the module doc above for
/// why that's a deliberate choice, not an arbitrary one.
pub const HEAP_START: u64 = 0x_1000_0000_000;
// 256 * 4KiB = 1MiB. Raised from the original 64 pages (256KiB) when the
// ring-3 scheduler grew from two tasks to three: each ring-3 task holds
// a private address space + kernel stack, and the extra task's heap
// footprint tipped the demo chain (specifically `experimental::isolation_demo`,
// which allocates a ~20KiB structure late in the sequence) past the old
// limit -- a real "memory allocation failed" panic, caught by the boot
// test. The larger heap is still far below every other mapped region
// (next is user code at 0x200_0000_0000).
pub const HEAP_SIZE_PAGES: u64 = 256;
pub const HEAP_SIZE_BYTES: usize = (HEAP_SIZE_PAGES * 4096) as usize;

#[global_allocator]
static ALLOCATOR: LockedHeap = LockedHeap::empty();

#[derive(Debug)]
pub enum HeapInitError {
    /// `mapped` pages were successfully mapped before running out of
    /// physical frames or hitting a mapping error; fewer than
    /// `HEAP_SIZE_PAGES` were requested.
    PartialMapping { mapped: u64, requested: u64 },
}

/// # Safety
/// Must be called exactly once, during `BootPhase::MemoryInit`, after
/// `paging::init()` and with a `BumpFrameAllocator` that hasn't been
/// used for anything the heap itself would then overwrite.
pub unsafe fn init(
    mapper: &mut OffsetPageTable<'static>,
    frame_allocator: &mut BumpFrameAllocator,
) -> Result<(), HeapInitError> {
    let flags = PageTableFlags::PRESENT | PageTableFlags::WRITABLE | PageTableFlags::NO_EXECUTE; // heap memory, never meant to be executed
    let mapped = paging::map_range(
        mapper,
        frame_allocator,
        VirtAddr::new(HEAP_START),
        HEAP_SIZE_PAGES,
        flags,
    );
    if mapped != HEAP_SIZE_PAGES {
        return Err(HeapInitError::PartialMapping {
            mapped,
            requested: HEAP_SIZE_PAGES,
        });
    }

    ALLOCATOR.lock().init(HEAP_START as *mut u8, HEAP_SIZE_BYTES);
    Ok(())
}
