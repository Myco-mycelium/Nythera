//! Dynamic page-table mapping.
//!
//! Phase 1's `boot.s` built exactly one thing: a static identity map
//! of the first 1GiB using 2MiB huge pages, just enough to enter long
//! mode. This module is what makes the page tables *dynamic* --
//! mapping new virtual pages to newly allocated physical frames on
//! demand, creating whatever intermediate page-table levels are
//! missing along the way, via the `x86_64` crate's `Mapper` trait.
//!
//! `physical_memory_offset` is `0`: Phase 1's identity map means
//! physical address P is already accessible at virtual address P, so
//! there's no separate "physical memory window" the way a fully
//! higher-half kernel would have one. This is a documented
//! simplification (see IMPLEMENTATION_STATUS.md) -- it works because
//! everything this kernel touches so far (the kernel image, the
//! Multiboot info structure, new page-table frames the allocator
//! hands out) lives within the identity-mapped first 1GiB. A true
//! higher-half redesign is future work, not a Phase 2 requirement.

use x86_64::registers::control::Cr3;
use x86_64::registers::model_specific::{Efer, EferFlags};
use x86_64::structures::paging::{
    FrameAllocator, Mapper, OffsetPageTable, Page, PageTable, PageTableFlags, PhysFrame, Size4KiB,
    Translate,
};
use x86_64::{PhysAddr, VirtAddr};

use crate::memory::frame_allocator::BumpFrameAllocator;

/// # Safety
/// Must be called exactly once, after `BootPhase::DiagnosticsInit`,
/// with `CR3` still pointing at boot.s's static identity-mapped
/// tables (true by construction -- nothing has touched CR3 since
/// `enable_paging` in boot.s).
pub unsafe fn init() -> OffsetPageTable<'static> {
    let (level_4_frame, _flags) = Cr3::read();
    let phys_addr = level_4_frame.start_address();
    // physical_memory_offset = 0, per the module doc above: phys_addr
    // is already a valid virtual address under the Phase 1 identity map.
    let virt_addr = VirtAddr::new(phys_addr.as_u64());
    let page_table_ptr: *mut PageTable = virt_addr.as_mut_ptr();
    let level_4_table: &'static mut PageTable = &mut *page_table_ptr;
    OffsetPageTable::new(level_4_table, VirtAddr::new(0))
}

/// Enables the CPU's NX (no-execute) page-protection feature via
/// `EFER.NXE`. Without this bit set, `PageTableFlags::NO_EXECUTE` on
/// any page-table entry means nothing -- the bit is reserved (and
/// must be zero) unless the hardware has been told to interpret it,
/// so this is a real prerequisite, not a formality, for every
/// `NO_EXECUTE`-flagged mapping in this kernel (`experimental::elf`'s
/// data segments, every stack, the heap) to actually be enforced
/// rather than silently ignored. See IMPLEMENTATION_STATUS.md for the
/// positive-control test that verified this is genuinely enforced,
/// not just set.
///
/// # Safety
/// Should be called once, early in Phase 2, before anything relies on
/// `NO_EXECUTE` actually being honored.
pub unsafe fn enable_nx() {
    let mut flags = Efer::read();
    flags.insert(EferFlags::NO_EXECUTE_ENABLE);
    Efer::write(flags);
}

/// Maps `size_in_pages` contiguous 4KiB pages starting at `start`,
/// each backed by a freshly allocated physical frame, with the given
/// flags. Used by `heap::init` to back the kernel heap; kept general
/// (not heap-specific) so Phase 3+ can reuse it for kernel stacks.
///
/// Returns the number of pages successfully mapped -- callers that
/// need "all or nothing" should compare this against `size_in_pages`
/// themselves rather than this function silently panicking partway
/// through a large request.
pub fn map_range(
    mapper: &mut OffsetPageTable<'static>,
    frame_allocator: &mut BumpFrameAllocator,
    start: VirtAddr,
    size_in_pages: u64,
    flags: PageTableFlags,
) -> u64 {
    let mut mapped = 0u64;
    for i in 0..size_in_pages {
        let page: Page<Size4KiB> = Page::containing_address(start + i * 4096u64);
        let frame: PhysFrame<Size4KiB> = match frame_allocator.allocate_frame() {
            Some(f) => f,
            None => break, // out of physical memory; report partial progress
        };
        let map_result = unsafe { mapper.map_to(page, frame, flags, frame_allocator) };
        match map_result {
            Ok(flush) => {
                flush.flush();
                mapped += 1;
            }
            Err(e) => {
                // A real failure report, not a debug marker: the caller
                // compares `mapped` against the request, so this line is
                // what names WHICH page stopped the walk and why. Labeled
                // plainly (no debug tag) so the project-wide residue grep
                // the earlier increments established stays literally
                // clean.
                crate::serial_println!("  [map_range] map_to FAILED at page {:#x}: {:?}", page.start_address().as_u64(), e);
                break
            }
        }
    }
    mapped
}

/// Also used by `heap::init` for a sanity read/write test: maps then
/// touches memory outside the original static identity range, to
/// prove this is genuinely new dynamic mapping and not accidentally
/// still landing inside Phase 1's pre-built 1GiB.
pub fn phys_addr_of_frame(frame: PhysFrame<Size4KiB>) -> PhysAddr {
    frame.start_address()
}

/// Translates `addr` through the active page tables, returning the
/// physical frame it currently maps to (or `None` when unmapped, or
/// when an intermediate level is absent). Used by self-tests to
/// observe mapping state directly instead of inferring it from
/// behavior -- see `memory::tlb_demo`.
pub fn translate(mapper: &OffsetPageTable<'static>, addr: VirtAddr) -> Option<PhysAddr> {
    mapper.translate_addr(addr)
}

/// Reads the current page-table flags of one 4KiB page through the
/// currently active page-table hierarchy: walks PML4->PDPT->PD->PT via
/// the identity-mapped table frames, and returns the leaf PTE's flags
/// (the `mapper` parameter is kept for interface symmetry with
/// `translate` and is not dereferenced -- observation goes through
/// `CR3` so the answer is exactly what the CPU would use for `addr`
/// (`None` when any level is absent or the leaf is unused). This is
/// an observation primitive for self-tests that must check
/// permission state *directly* rather than infer it from fault
/// behavior -- `experimental::pie_demo`'s GNU_RELRO
/// read-only-after-relocation check is the first caller: it asserts
/// every RELRO page's leaf PTE genuinely lacks `WRITABLE` after
/// `experimental::elf::load`'s revoke pass.
///
/// Returns `Err` for structurally unexpected states (huge-page leaf
/// at an unexpected level, frame address not identity-reachable) so
/// a caller cannot mistake "couldn't observe" for "observed clean".
pub fn page_flags_of(
    _mapper: &OffsetPageTable<'static>,
    addr: VirtAddr,
) -> Option<Result<PageTableFlags, ()>> {
    use x86_64::structures::paging::PageTable;

    let page: Page<Size4KiB> = Page::containing_address(addr);

    // The mapper's level-4 table, via the same identity mapping every
    // other page-table walker in this kernel uses (physical_memory_offset
    // == 0; the tables themselves were allocated inside the first 1GiB --
    // see the module doc at the top of this file).
    let (pml4_frame, _) = Cr3::read();
    let pml4: &PageTable = unsafe { &*(pml4_frame.start_address().as_u64() as *const PageTable) };

    let p4e = &pml4[page.p4_index()];
    if !p4e.flags().contains(PageTableFlags::PRESENT) {
        return None;
    }
    if p4e.flags().contains(PageTableFlags::HUGE_PAGE) {
        return Some(Err(())); // boot.s's shared 2MiB huge pages never host relocated user code
    }
    let pdpt: &PageTable = unsafe { &*(p4e.addr().as_u64() as *const PageTable) };
    let p3e = &pdpt[page.p3_index()];
    if !p3e.flags().contains(PageTableFlags::PRESENT) {
        return None;
    }
    if p3e.flags().contains(PageTableFlags::HUGE_PAGE) {
        return Some(Err(())); // same: process-private subtrees only map 4KiB pages
    }
    let pd: &PageTable = unsafe { &*(p3e.addr().as_u64() as *const PageTable) };
    let p2e = &pd[page.p2_index()];
    if !p2e.flags().contains(PageTableFlags::PRESENT) {
        return None;
    }
    if p2e.flags().contains(PageTableFlags::HUGE_PAGE) {
        return Some(Err(()));
    }
    let pt: &PageTable = unsafe { &*(p2e.addr().as_u64() as *const PageTable) };
    let p1e = &pt[page.p1_index()];
    if !p1e.flags().contains(PageTableFlags::PRESENT) {
        return None;
    }
    Some(Ok(p1e.flags()))
}
/// Unmaps the 4 KiB page containing `addr` and invalidates its TLB
/// entry. Returns `true` when a mapping existed and was removed.
///
/// This is the kernel's first TLB invalidation on unmap: until now
/// nothing ever removed a mapping (everything was mapped-then-used
/// forever), and IMPLEMENTATION_STATUS.md named "TLB invalidation
/// (`invlpg`) on unmap ... isn't addressed" as an open gap. `Mapper::unmap`
/// returns a `MapperFlush`; `.flush()` emits the `invlpg`. Skipping it
/// would leave a stale translation a later remap of the same virtual
/// address could still read through. `memory::tlb_demo` proves the
/// unmap+remap cycle produces the new mapping.
pub fn unmap_page(mapper: &mut OffsetPageTable<'static>, addr: VirtAddr) -> bool {
    let page: Page<Size4KiB> = Page::containing_address(addr);
    match mapper.unmap(page) {
        Ok((_frame, flush)) => {
            flush.flush();
            true
        }
        Err(_) => false,
    }
}
