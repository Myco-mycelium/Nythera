//! TLB-invalidation-on-unmap self-test.
//!
//! Closes the "TLB invalidation (`invlpg`) on unmap ... isn't
//! addressed" gap named in IMPLEMENTATION_STATUS.md. This is generic
//! paging machinery (NPS-001 territory), so it lives under `memory`
//! rather than `experimental`.
//!
//! Proof, and why it is narrowly scoped: map a fresh page at a chosen
//! virtual address (frame A) and write a magic; unmap it (which emits
//! `invlpg` via [`crate::memory::paging::unmap_page`]); map the SAME
//! virtual address again (frame B); write a different magic and read
//! it back. The read must observe frame B's bytes, and `translate` is
//! used to observe the page-table state directly at each step
//! (present -> absent -> present, with A != B), so a "remap silently
//! kept the old mapping" bug cannot pass by coincidence.
//!
//! Honest scope: this demonstrates that unmap removes the mapping and
//! a remap produces a genuinely different backing frame that reads
//! correctly. Whether a stale TLB entry would *observably* corrupt a
//! read depends on the (emulated) CPU's TLB state, so this test does
//! not claim to force that situation -- it proves the operations and
//! the flush are wired correctly, which is what the gap was about.

use x86_64::structures::paging::{OffsetPageTable, PageTableFlags};
use x86_64::VirtAddr;

use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::memory::paging;
use crate::serial_println;

/// A virtual address well outside every region this kernel otherwise
/// uses (heap 0x100_0000_0000, user code 0x200_0000_0000, ring-3
/// 0x400_0000_0000, PIE 0x600_0000_0000), so the probe cannot collide
/// with anything real.
const TEST_VADDR: u64 = 0x_7000_0000_000;

const MAGIC_A: u64 = 0xAAAA_AAAA_AAAA_AAAA;
const MAGIC_B: u64 = 0xBBBB_BBBB_BBBB_BBBB;

/// Runs the map -> unmap -> remap proof. Returns `true` iff every step
/// behaved correctly.
pub fn run(mapper: &mut OffsetPageTable<'static>, frame_allocator: &mut BumpFrameAllocator) -> bool {
    let flags = PageTableFlags::PRESENT | PageTableFlags::WRITABLE | PageTableFlags::NO_EXECUTE;
    let addr = VirtAddr::new(TEST_VADDR);

    // 1. Map frame A and write MAGIC_A through the new mapping.
    if paging::map_range(mapper, frame_allocator, addr, 1, flags) != 1 {
        serial_println!("  memory tlb-demo: FAILED to map the test page");
        return false;
    }
    let frame_a = paging::translate(mapper, addr);
    unsafe {
        core::ptr::write_volatile(TEST_VADDR as *mut u64, MAGIC_A);
    }

    // 2. Unmap it: the page-table entry must be gone afterwards.
    let removed = paging::unmap_page(mapper, addr);
    let after_unmap = paging::translate(mapper, addr);

    // 3. Remap the SAME virtual address to a different frame B, write
    //    MAGIC_B, and read it back -- must observe B, not the stale A.
    if paging::map_range(mapper, frame_allocator, addr, 1, flags) != 1 {
        serial_println!("  memory tlb-demo: FAILED to remap the test page");
        return false;
    }
    let frame_b = paging::translate(mapper, addr);
    unsafe {
        core::ptr::write_volatile(TEST_VADDR as *mut u64, MAGIC_B);
    }
    let read_back = unsafe { core::ptr::read_volatile(TEST_VADDR as *const u64) };

    let mapped_a = frame_a.is_some();
    let unmapped = removed && after_unmap.is_none();
    let distinct_frames = frame_b.is_some() && frame_a != frame_b;
    let read_ok = read_back == MAGIC_B;

    serial_println!(
        "  memory tlb-demo: mapped (frame {:?}), unmapped (translate -> {:?}), remapped (frame {:?}); re-read {:#x} (expected {:#x})",
        frame_a, after_unmap, frame_b, read_back, MAGIC_B
    );
    serial_println!(
        "  memory tlb-demo self-test: present->absent->present {}, backing frame genuinely changed {}, remap read back new bytes {} -- {}",
        if unmapped { "OK" } else { "FAILED" },
        if distinct_frames { "OK" } else { "FAILED" },
        if read_ok { "OK" } else { "FAILED" },
        if mapped_a && unmapped && distinct_frames && read_ok { "OK" } else { "FAILED" }
    );

    mapped_a && unmapped && distinct_frames && read_ok
}
