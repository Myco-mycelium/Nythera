//! Address-space teardown proof.
//!
//! Every other `experimental` demo `mem::forget`s its `AddressSpace`s
//! -- deliberate, documented, but still a real gap: nothing in this
//! kernel could previously reclaim a process's memory when it exited.
//! This module proves `AddressSpace::destroy` (and the frame
//! allocator's new free-list backing it) actually works: build a
//! space, use it, destroy it, and confirm the freed physical frames
//! come back for reuse -- not just that `destroy` ran without
//! panicking.
//!
//! See `crate::experimental`'s banner -- not NPS-002/NPS-003
//! conformance. Kernel-mode only (no ring-3 transition needed here;
//! this is testing memory-management bookkeeping, not privilege
//! mechanics, which other demos already cover separately).

use x86_64::registers::control::{Cr3, Cr3Flags};
use x86_64::structures::paging::{PageTableFlags, PhysFrame};
use x86_64::{PhysAddr, VirtAddr};

use crate::experimental::address_space::{self, AddressSpace};
use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::memory::paging;
use crate::serial_println;

const TEST_ADDR: u64 = 0x_7000_0000_000;
const TEST_MAGIC: u64 = 0xDEADC0DE_DEADC0DE;

fn restore_cr3(cr3_phys_addr: u64) {
    let frame = PhysFrame::containing_address(PhysAddr::new(cr3_phys_addr));
    unsafe { Cr3::write(frame, Cr3Flags::empty()) };
}

/// Returns `true` iff every part of the proof held: the space worked
/// correctly while alive, its frames were fully reclaimed on
/// `destroy`, and a subsequently-built space's PML4 landed on exactly
/// the physical frame `destroy` just freed (proving genuine reuse via
/// the free-list's LIFO ordering -- `destroy` frees the PML4 frame
/// last, so it's the first thing the next `allocate_frame` call pops).
pub fn run(frame_allocator: &mut BumpFrameAllocator) -> bool {
    let original_cr3 = crate::experimental::scheduler::current_cr3();
    let baseline_in_use = frame_allocator.frames_in_use();

    let mut space_x = AddressSpace::new_sharing_kernel(frame_allocator)
        .unwrap_or_else(|_| panic!("teardown-demo: failed to build space X"));
    unsafe { space_x.activate() };
    let pml4_x = address_space::pml4_phys_addr(&space_x);

    {
        let mut mapper = space_x.mapper();
        let flags = PageTableFlags::PRESENT | PageTableFlags::WRITABLE | PageTableFlags::NO_EXECUTE;
        let mapped = paging::map_range(&mut mapper, frame_allocator, VirtAddr::new(TEST_ADDR), 1, flags);
        assert_eq!(mapped, 1, "teardown-demo: failed to map test page in space X");
    }
    unsafe { core::ptr::write_volatile(TEST_ADDR as *mut u64, TEST_MAGIC) };
    let observed = unsafe { core::ptr::read_volatile(TEST_ADDR as *const u64) };
    let write_ok = observed == TEST_MAGIC;

    let in_use_before_destroy = frame_allocator.frames_in_use();

    // destroy()'s safety contract: must not be the active space when torn down.
    restore_cr3(original_cr3);
    unsafe { space_x.destroy(frame_allocator) };

    let in_use_after_destroy = frame_allocator.frames_in_use();
    let frames_reclaimed = in_use_before_destroy - in_use_after_destroy;
    let fully_reclaimed = in_use_after_destroy == baseline_in_use;

    // Build a second space immediately: its PML4 allocation should pop
    // X's just-freed PML4 frame straight back off the free list.
    let space_y = AddressSpace::new_sharing_kernel(frame_allocator)
        .unwrap_or_else(|_| panic!("teardown-demo: failed to build space Y"));
    let pml4_y = address_space::pml4_phys_addr(&space_y);
    let reused = pml4_x == pml4_y;

    serial_println!(
        "  experimental teardown-demo: space X used (wrote+read {:#x} -- {}), {} frames reclaimed on destroy ({} -> {} in-use, baseline {}) -- {}",
        observed,
        if write_ok { "OK" } else { "MISMATCH" },
        frames_reclaimed,
        in_use_before_destroy,
        in_use_after_destroy,
        baseline_in_use,
        if fully_reclaimed { "fully reclaimed" } else { "LEAK" }
    );
    serial_println!(
        "  experimental teardown-demo: space Y's PML4 ({:#x}) {} space X's just-freed PML4 ({:#x}) -- {}",
        pml4_y.as_u64(),
        if reused { "==" } else { "!=" },
        pml4_x.as_u64(),
        if reused { "OK, frame genuinely reused" } else { "not reused" }
    );

    // Y itself is still leaked deliberately -- one demonstrated
    // create/use/destroy/reuse cycle per boot is the proof; Y doesn't
    // need its own teardown to make the point.
    core::mem::forget(space_y);

    write_ok && fully_reclaimed && reused
}
