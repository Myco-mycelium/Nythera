//! Physical frame allocator.
//!
//! A bump allocator over the `Available` regions the Multiboot2
//! memory map reports, skipping (a) the low 1MiB (legacy BIOS/real-mode
//! reserved area -- not universally unsafe to use, but not worth the
//! risk for zero benefit this early) and (b) whatever physical range
//! the kernel image itself occupies (`_kernel_start.._kernel_end`,
//! from `linker.ld`), since those frames are already in active use and
//! handing them out again would silently corrupt the running kernel.
//!
//! Now backed by a real free-list, not bump-only: freed frames go onto
//! a `Vec<PhysFrame>` (heap-allocated -- the kernel heap's own
//! allocator, `linked_list_allocator`, already supports real
//! deallocation; it was only the *physical frame* allocator that
//! didn't) and are handed out again, LIFO, before the bump pointer
//! ever advances further. This exists specifically so
//! `experimental::address_space::AddressSpace::destroy` can return a
//! torn-down process's page-table and data frames for reuse -- without
//! it, every address space this kernel ever built would leak its
//! physical memory forever, which is exactly what happened before this
//! (every experimental demo `mem::forget`s its spaces to this day,
//! except the one built specifically to prove teardown -- see
//! PHASES.md).

use alloc::vec::Vec;
use crate::multiboot::{MemoryRegionType, MultibootInfo};

/// How many ACPI-reclaimable ranges we track. Fixed-size and
/// allocation-free on purpose: the reclaim path runs during early boot,
/// before the kernel heap exists, so it must not depend on `Vec`.
const MAX_ACPI_RANGES: usize = 16;
use x86_64::structures::paging::{FrameAllocator, FrameDeallocator, PhysFrame, Size4KiB};
use x86_64::PhysAddr;

extern "C" {
    /// Provided by linker.ld -- these are link-time addresses, not
    /// runtime data; taking their *address* (not dereferencing them)
    /// gives the kernel image's physical extent under Phase 1/2's
    /// identity mapping.
    static _kernel_start: u8;
    static _kernel_end: u8;
}

const LOW_MEMORY_RESERVED: u64 = 0x100000; // below 1MiB: legacy BIOS area

pub struct BumpFrameAllocator {
    multiboot: MultibootInfo,
    next_frame_number: u64,
    kernel_start_frame: u64,
    kernel_end_frame: u64,
    frames_allocated: u64,
    frames_freed: u64,
    /// Freed frames, reused LIFO before the bump pointer advances --
    /// deliberately a plain `Vec`, not a fancier structure: this
    /// kernel's total frame usage is small enough (hundreds, not
    /// millions) that a `Vec`'s O(1) push/pop is plenty, and the
    /// simplicity keeps this auditable the same way the rest of this
    /// kernel's memory-management code has been.
    free_list: Vec<PhysFrame<Size4KiB>>,
    /// ACPI-reclaimable physical ranges that have been released for
    /// reuse by `crate::acpi` (after its tables were read). Empty until
    /// `reclaim_acpi` is called, so the default posture is "refuse",
    /// not "assume free" -- see `crate::acpi`'s module doc for why
    /// reclaiming before the tables are read would be wrong.
    acpi_reclaimable: [(u64, u64); MAX_ACPI_RANGES],
    acpi_reclaimable_count: usize,
}

impl BumpFrameAllocator {
    pub fn new(multiboot: MultibootInfo) -> Self {
        let kernel_start = unsafe { &_kernel_start as *const u8 as u64 };
        let kernel_end = unsafe { &_kernel_end as *const u8 as u64 };
        BumpFrameAllocator {
            multiboot,
            next_frame_number: LOW_MEMORY_RESERVED / 4096,
            kernel_start_frame: kernel_start / 4096,
            kernel_end_frame: (kernel_end + 4095) / 4096, // round up
            frames_allocated: 0,
            frames_freed: 0,
            free_list: Vec::new(),
            acpi_reclaimable: [(0, 0); MAX_ACPI_RANGES],
            acpi_reclaimable_count: 0,
        }
    }

    /// Cumulative frames ever handed out by `allocate_frame` --
    /// includes frames later freed and possibly re-handed-out, so this
    /// is a running total, not a "currently in use" count. See
    /// [`frames_in_use`](Self::frames_in_use) for that.
    pub fn frames_allocated(&self) -> u64 {
        self.frames_allocated
    }

    pub fn frames_freed(&self) -> u64 {
        self.frames_freed
    }

    /// `frames_allocated - frames_freed` -- how many frames are
    /// actually outstanding right now. This is what
    /// `experimental::teardown_demo` checks to prove a destroyed
    /// address space's frames genuinely came back, not just that
    /// `destroy` ran without panicking.
    pub fn frames_in_use(&self) -> u64 {
        self.frames_allocated - self.frames_freed
    }

    /// Total usable frames reported by the memory map (informational --
    /// used only for the boot-diagnostics print, not allocation logic).
    pub fn total_usable_frames(&self) -> u64 {
        let mut total = 0u64;
        self.multiboot.for_each_memory_region(|region| {
            if region.region_type == MemoryRegionType::Available {
                total += region.length / 4096;
            }
        });
        total
    }

    /// Releases every `AcpiReclaimable` region the memory map reports
    /// for reuse, returning how many were added. Must only be called
    /// once `crate::acpi` has validated the RSDP and read the tables --
    /// the spec permits reusing this memory only after the OS is done
    /// with it. Idempotent-safe in practice (re-adding a region is
    /// harmless, it's already usable), but intended to be called once.
    pub fn reclaim_acpi(&mut self) -> usize {
        // `self.multiboot` is `Copy`, so iterating it through a local
        // avoids holding a borrow of `self` while mutating the reclaim
        // list inside the closure.
        let multiboot = self.multiboot;
        let mut count = 0usize;
        multiboot.for_each_memory_region(|region| {
            if region.region_type == MemoryRegionType::AcpiReclaimable {
                self.add_reclaimable_range(region.base_addr, region.length);
                count += 1;
            }
        });
        count
    }

    /// Public form of the internal usability check, used by
    /// `kernel_main`'s reclaim self-test to observe the before/after
    /// state directly rather than inferring it.
    pub fn frame_is_usable(&self, frame_number: u64) -> bool {
        self.is_frame_usable(frame_number)
    }

    /// Releases one physical range for reuse. Used by [`reclaim_acpi`]
    /// and by the reclaim self-test's synthetic control (some firmware
    /// -- including every QEMU machine type this kernel has been booted
    /// on -- reports no `AcpiReclaimable` region at all, so the release
    /// behaviour needs a deterministic check that doesn't depend on the
    /// firmware happening to provide one).
    ///
    /// [`reclaim_acpi`]: Self::reclaim_acpi
    pub fn add_reclaimable_range(&mut self, base: u64, length: u64) {
        if self.acpi_reclaimable_count < MAX_ACPI_RANGES {
            self.acpi_reclaimable[self.acpi_reclaimable_count] = (base, length);
            self.acpi_reclaimable_count += 1;
        }
    }

    /// Removes a previously-added range (by base address) from the
    /// released set, so a synthetic control leaves no lasting state.
    /// Order is not preserved (swap-remove) -- the list is a set.
    pub fn remove_reclaimable_range(&mut self, base: u64) {
        let mut i = 0;
        while i < self.acpi_reclaimable_count {
            if self.acpi_reclaimable[i].0 == base {
                let last = self.acpi_reclaimable_count - 1;
                self.acpi_reclaimable[i] = self.acpi_reclaimable[last];
                self.acpi_reclaimable_count = last;
                return;
            }
            i += 1;
        }
    }

    fn is_frame_usable(&self, frame_number: u64) -> bool {
        if frame_number >= self.kernel_start_frame && frame_number < self.kernel_end_frame {
            return false; // overlaps the running kernel image
        }
        let frame_addr = frame_number * 4096;
        if self.acpi_reclaimable[..self.acpi_reclaimable_count]
            .iter()
            .any(|&(base, len)| frame_addr >= base && frame_addr < base + len)
        {
            return true; // an ACPI-reclaimable range released by `crate::acpi`
        }
        let mut usable = false;
        self.multiboot.for_each_memory_region(|region| {
            if usable || region.region_type != MemoryRegionType::Available {
                return;
            }
            if frame_addr >= region.base_addr && frame_addr < region.base_addr + region.length {
                usable = true;
            }
        });
        usable
    }
}

/// `x86_64` crate's `FrameAllocator` trait -- implementing this is what
/// lets the paging module's `Mapper::map_to` create new page-table
/// levels on demand instead of requiring them to already exist.
unsafe impl FrameAllocator<Size4KiB> for BumpFrameAllocator {
    fn allocate_frame(&mut self) -> Option<PhysFrame<Size4KiB>> {
        if let Some(frame) = self.free_list.pop() {
            self.frames_allocated += 1;
            return Some(frame);
        }
        // Bounded search: try up to 1M frames (4GiB of address space)
        // past the current bump pointer before giving up, so a
        // pathological memory map can't hang the allocator in an
        // infinite scan.
        for _ in 0..1_000_000u32 {
            let candidate = self.next_frame_number;
            self.next_frame_number += 1;
            if self.is_frame_usable(candidate) {
                self.frames_allocated += 1;
                return Some(PhysFrame::containing_address(PhysAddr::new(candidate * 4096)));
            }
        }
        None
    }
}

/// The `x86_64` crate's counterpart trait to `FrameAllocator` --
/// implementing it is what lets `AddressSpace::destroy` return frames
/// through the same object that handed them out, rather than needing
/// a second, separate deallocation path.
///
/// # Safety
/// The caller must ensure `frame` is not still referenced by any live
/// page-table entry anywhere -- freeing a frame that's still mapped
/// and then having it handed out again would let two different
/// mappings alias the same physical memory unintentionally, which is
/// exactly the class of bug this kernel's whole page-table-isolation
/// story exists to prevent.
impl FrameDeallocator<Size4KiB> for BumpFrameAllocator {
    unsafe fn deallocate_frame(&mut self, frame: PhysFrame<Size4KiB>) {
        self.frames_freed += 1;
        self.free_list.push(frame);
    }
}
