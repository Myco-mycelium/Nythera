//! Per-process address spaces.
//!
//! Everything in `experimental::usermode`/`experimental::elf` so far
//! ran ring-3 code in the kernel's *own* page tables -- real isolation
//! between processes (or between a process and the kernel) didn't
//! exist. This module is what actually creates that isolation: a
//! fresh top-level page table (PML4) per process, sharing exactly the
//! entries that must stay shared (the kernel's own identity-mapped low
//! memory and the kernel heap -- both needed so the kernel keeps
//! working, interrupts keep being deliverable, and `serial_println!`
//! keeps working, regardless of which process's address space is
//! currently active) while giving each process private, unshared
//! entries for its own code and stack.
//!
//! See `crate::experimental`'s module banner: this is not NPS-002
//! conformance. What it proves, checkably, via the two-process demo in
//! `kernel_main`: two processes using the *identical* virtual address
//! for their code/stack genuinely cannot see or corrupt each other's
//! memory -- process A's data survives process B running and writing
//! to "the same" address, because it was never actually the same
//! physical memory.
//!
//! **Known simplification, documented rather than hidden:** building a
//! new address space needs to read the *currently active* PML4's
//! entries directly (to copy the shared ones), which means treating
//! physical addresses as directly accessible virtual addresses -- true
//! under this kernel's identity-mapped-low-memory design only as long
//! as the frame allocator keeps handing out frames from within the
//! first 1GiB, which it does at this kernel's current, modest memory
//! usage, but isn't a general guarantee. A production kernel needs an
//! explicit physical-memory access strategy (a physmap window); that's
//! real future work, not something this proof of concept solves.

use x86_64::registers::control::{Cr3, Cr3Flags};
use x86_64::structures::paging::{
    FrameDeallocator, Mapper, OffsetPageTable, Page, PageTable, PageTableFlags, PhysFrame, Size4KiB,
};
use x86_64::{PhysAddr, VirtAddr};

use crate::gdt;
use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::memory::kernel_stack::{self, GuardedStack};

/// Which top-level (PML4) entries are shared across every address
/// space this kernel creates. Index 0 covers virtual addresses
/// `0x0..0x8000000000` (512GiB) -- boot.s's identity-mapped first
/// 1GiB, the kernel image, the IDT/GDT, and this kernel's stack all
/// live there. Index 2 covers `0x10000000000..0x18000000000`, which
/// is where `memory::heap::HEAP_START` (`0x10000000000`) falls --
/// sharing it means every process's `int 0x80` handler can still use
/// `alloc::*` inside the kernel, since it's the same kernel heap
/// regardless of which process trapped in.
const SHARED_PML4_INDICES: [usize; 2] = [0, 2];

pub struct AddressSpace {
    pml4_frame: PhysFrame,
    /// This address space's own private RSP0 stack -- the stack the CPU
    /// switches to on a ring-3->ring-0 trap while this space is active.
    /// Kept alive for as long as the `AddressSpace` is (typically
    /// forever, via `mem::forget`, same as everything else in this
    /// proof of concept with no teardown story yet).
    ///
    /// Unlike the heap `Vec<u8>` this used to be, the stack now comes
    /// from `memory::kernel_stack`'s shared region and carries a real
    /// guard page directly below it, so a kernel stack overflow faults
    /// deterministically instead of silently corrupting the heap. See
    /// that module for why the region has to be shared (the context
    /// switch changes `CR3` before `RSP`).
    kernel_stack: GuardedStack,
}

#[derive(Debug)]
pub struct AddressSpaceError;

impl AddressSpace {
    /// Allocates a fresh PML4, zeroes it, then copies just the shared
    /// entries (see [`SHARED_PML4_INDICES`]) from whichever address
    /// space is currently active, and allocates this space's own
    /// private RSP0 stack. Does NOT activate the new space -- see
    /// [`activate`](Self::activate).
    pub fn new_sharing_kernel(frame_allocator: &mut BumpFrameAllocator) -> Result<Self, AddressSpaceError> {
        use x86_64::structures::paging::FrameAllocator;
        let pml4_frame = frame_allocator.allocate_frame().ok_or(AddressSpaceError)?;

        let pml4_virt = VirtAddr::new(pml4_frame.start_address().as_u64());
        let (current_pml4_frame, _) = Cr3::read();
        let current_pml4: &PageTable =
            unsafe { &*(current_pml4_frame.start_address().as_u64() as *const PageTable) };
        let new_pml4: &mut PageTable = unsafe { &mut *pml4_virt.as_mut_ptr() };

        new_pml4.zero();
        for &i in SHARED_PML4_INDICES.iter() {
            new_pml4[i] = current_pml4[i].clone();
        }

        // Allocate this space's RSP0 stack from the shared guarded
        // region. Mapping through *this* space's PML4 is correct even
        // though it is not active: the region lives under the shared
        // index-2 subtree, so the entries written here are the ones
        // every space (including the kernel's own) resolves through.
        let mut mapper = unsafe { OffsetPageTable::new(&mut *pml4_virt.as_mut_ptr(), VirtAddr::new(0)) };
        let kernel_stack = kernel_stack::allocate(&mut mapper, frame_allocator, kernel_stack::DEFAULT_STACK_PAGES)
            .ok_or(AddressSpaceError)?;

        Ok(AddressSpace { pml4_frame, kernel_stack })
    }

    /// Switches `CR3` to this address space AND `TSS.RSP0` to this
    /// space's own private kernel stack. Safe to call repeatedly to
    /// switch back and forth between address spaces that have already
    /// been built -- that's exactly what the two-process demo does to
    /// prove isolation (A -> B -> back to A, checking A's data
    /// survived), and now each of A/B has its own RSP0 for that
    /// switch to update too, not just CR3.
    ///
    /// # Safety
    /// The caller must ensure the shared entries (kernel code, IDT/GDT,
    /// heap) really are present and correct in this table -- true for
    /// anything built via [`new_sharing_kernel`], not necessarily true
    /// for a hand-constructed one.
    pub unsafe fn activate(&self) {
        Cr3::write(self.pml4_frame, Cr3Flags::empty());
        gdt::set_rsp0(self.kernel_stack.top);
    }

    /// Builds an `OffsetPageTable` for mapping into *this* address
    /// space. Only valid to use for mapping calls while this space is
    /// the active one (i.e. right after [`activate`](Self::activate)) --
    /// `experimental::elf::load`/`memory::paging::map_range` write
    /// through the currently active page tables, not through an
    /// arbitrary inactive one.
    pub fn mapper(&mut self) -> OffsetPageTable<'static> {
        let virt_addr = VirtAddr::new(self.pml4_frame.start_address().as_u64());
        let table: &'static mut PageTable = unsafe { &mut *virt_addr.as_mut_ptr() };
        unsafe { OffsetPageTable::new(table, VirtAddr::new(0)) }
    }

    /// The top (highest address) of this space's private RSP0 stack --
    /// needed by `experimental::ring3_scheduler` to hand-construct a
    /// not-yet-run task's initial saved-trap-context blob, which must
    /// live on that task's own kernel stack, not the caller's.
    pub fn kernel_stack_top(&self) -> u64 {
        self.kernel_stack.top.as_u64()
    }

    /// This space's guarded RSP0 stack descriptor -- used by
    /// `experimental::ring3_scheduler` to verify, structurally, that
    /// the live stacks really do carry a guard page.
    pub fn kernel_stack(&self) -> &GuardedStack {
        &self.kernel_stack
    }

    /// Frees every frame this address space exclusively owns: its own
    /// private page-table hierarchy (PDPT/PD/PT frames created by
    /// `Mapper::map_to` while mapping this process's code/stack/data)
    /// and every data frame those tables point to, then the PML4 frame
    /// itself. Deliberately skips [`SHARED_PML4_INDICES`] entirely --
    /// those point at the kernel's own identity map and heap, which
    /// every *other* address space still needs; freeing them here
    /// would corrupt every other live process (including the kernel
    /// itself) the moment this one exits.
    ///
    /// `self.kernel_stack` (the guarded RSP0 stack) *is* reclaimed here
    /// too, even though it lives in `memory::kernel_stack`'s shared
    /// region: that region hands out a fresh slot per allocation and
    /// never reuses one, so this slot is owned exclusively by this
    /// space. Clearing its page-table entries through the currently
    /// active (shared) tables and freeing their data frames therefore
    /// cannot affect any other space -- only the shared PDPT/PD/PT
    /// frames that hold those entries outlive every space.
    ///
    /// Must NOT be called while this space is the currently active one
    /// (its own page tables, including the ones about to be freed,
    /// wouldn't be safe to keep using mid-teardown) -- the caller must
    /// switch to a different space first.
    ///
    /// # Safety
    /// No code anywhere must still be relying on any mapping this
    /// space owns -- in particular, this must not be called on a space
    /// some suspended task might still be resumed into later (nothing
    /// in this kernel currently does that after calling `destroy`, but
    /// nothing enforces it either; this is a real, documented sharp
    /// edge, not a hidden one).
    pub unsafe fn destroy(self, frame_allocator: &mut BumpFrameAllocator) {
        // Reclaim the guarded kernel stack first. Its slot is ours
        // alone, but its page-table entries live in the *shared*
        // index-2 subtree, so they must be cleared through whichever
        // space is currently active (which shares that subtree).
        {
            let (active_pml4, _) = Cr3::read();
            let active: &'static mut PageTable =
                &mut *(active_pml4.start_address().as_u64() as *mut PageTable);
            let mut mapper = OffsetPageTable::new(active, VirtAddr::new(0));
            for i in 0..self.kernel_stack.pages {
                let page: Page<Size4KiB> =
                    Page::containing_address(self.kernel_stack.base + i * 4096);
                if let Ok((frame, flush)) = mapper.unmap(page) {
                    flush.flush();
                    frame_allocator.deallocate_frame(frame);
                }
            }
        }

        let pml4_virt = VirtAddr::new(self.pml4_frame.start_address().as_u64());
        let pml4: &mut PageTable = &mut *pml4_virt.as_mut_ptr();

        for i in 0..512 {
            if SHARED_PML4_INDICES.contains(&i) {
                continue;
            }
            let entry = &pml4[i];
            if entry.flags().contains(PageTableFlags::PRESENT) {
                if let Ok(child_frame) = entry.frame() {
                    free_table_tree(child_frame, 3, frame_allocator); // 3 = PDPT level
                }
            }
        }

        // Freed last so it is the first frame the next allocation pops
        // off the free list; `experimental::teardown_demo` relies on
        // exactly that to prove genuine frame reuse.
        frame_allocator.deallocate_frame(self.pml4_frame);
    }
}

/// Recursively frees a page-table-tree frame at `level` (3 = PDPT,
/// 2 = PD, 1 = PT) and everything it points to. At `level == 1`
/// (a PT), entries point to real data frames, not further tables --
/// freed directly rather than recursed into, since there's no level 0.
///
/// This kernel's own mappings never create huge (2MiB/1GiB) pages
/// within a process-private subtree (`memory::paging::map_range`
/// always maps `Size4KiB` pages via `Mapper<Size4KiB>`) -- the only
/// huge pages anywhere are boot.s's original identity map, which lives
/// entirely under the *shared* `SHARED_PML4_INDICES`, never reached by
/// this walk. A huge-page entry encountered here would indicate a
/// logic error elsewhere, not a case this function needs to handle.
unsafe fn free_table_tree(frame: PhysFrame, level: u8, frame_allocator: &mut BumpFrameAllocator) {
    let virt = VirtAddr::new(frame.start_address().as_u64());
    let table: &mut PageTable = &mut *virt.as_mut_ptr();

    for entry in table.iter() {
        if !entry.flags().contains(PageTableFlags::PRESENT) {
            continue;
        }
        if let Ok(child) = entry.frame() {
            if level > 1 {
                free_table_tree(child, level - 1, frame_allocator);
            } else {
                frame_allocator.deallocate_frame(child); // a real data page, not a table
            }
        }
    }
    frame_allocator.deallocate_frame(frame); // the table itself
}

/// Read back the raw physical address of an address space's PML4 --
/// used only for diagnostic printing (proving to a human reading the
/// boot log that A and B really are backed by different tables, not
/// just asserted to be).
pub fn pml4_phys_addr(space: &AddressSpace) -> PhysAddr {
    space.pml4_frame.start_address()
}
