//! Pre-normative shared-memory regions (NPS-003 Draft §3.1, second half;
//! closes FIND-CONTAINER-003 in this experimental tree).
//!
//! * A region is ONE physical frame, **zeroed by the kernel at creation**
//!   (never by the creator, never trusting recycled-frame contents).
//! * User code reaches a region only through a `Kind::SharedMem`
//!   capability (rights READ / WRITE / GRANT): attenuable and
//!   transferable exactly like every other capability. Mapping needs
//!   READ or WRITE; the page is mapped writable only with WRITE.
//! * Lifetime = CAPABILITY HOLDERS (tables and in-flight messages, counted by
//!   `capability`): when the last capability disappears the region is DEAD (new
//!   maps fail with TargetDead); its frame is freed when the last existing
//!   mapping is torn down. The creator exiting does NOT kill a region that others hold. Every mapping is
//!   counted, so address-space teardown never double-frees a shared frame.
//!
//! Not modelled: multi-page regions, unmap-by-user, resizing, and
//! cache-colouring/NUMA. Single core: no concurrent mapping races.

use alloc::vec::Vec;
use x86_64::structures::paging::{FrameAllocator, FrameDeallocator, PhysFrame, Size4KiB};

use crate::memory::frame_allocator::BumpFrameAllocator;

pub type RegionId = u32;

/// Virtual window user code may map regions into (16 pages, inside the
/// demo processes' data area; cannot overlap code, stack or the data page).
pub const WINDOW_BASE: u64 = 0x_4000_0100_000;
pub const WINDOW_PAGES: u64 = 16;
pub const PAGE: usize = 4096;

#[derive(Debug, PartialEq, Eq)]
pub enum ShmError {
    OutOfMemory,
    BadRegion,
    Dead,
}

struct Region {
    frame: PhysFrame<Size4KiB>,
    alive: bool,
    maps: u32,
    freed: bool,
}

pub struct ShmTable {
    regions: Vec<Region>,
}

impl ShmTable {
    pub const fn new() -> Self {
        ShmTable { regions: Vec::new() }
    }

    /// Allocates and ZEROES a frame. The kernel does the zeroing itself,
    /// regardless of what the allocator hands back.
    pub fn create(&mut self, fa: &mut BumpFrameAllocator) -> Result<RegionId, ShmError> {
        let frame = fa.allocate_frame().ok_or(ShmError::OutOfMemory)?;
        unsafe { core::ptr::write_bytes(frame.start_address().as_u64() as *mut u8, 0, PAGE) };
        self.regions.push(Region { frame, alive: true, maps: 0, freed: false });
        Ok((self.regions.len() - 1) as RegionId)
    }

    /// The frame to map, only while the region is alive.
    pub fn frame_for_map(&self, id: RegionId) -> Result<PhysFrame<Size4KiB>, ShmError> {
        let r = self.regions.get(id as usize).ok_or(ShmError::BadRegion)?;
        if !r.alive || r.freed { return Err(ShmError::Dead); }
        Ok(r.frame)
    }

    pub fn note_mapped(&mut self, id: RegionId) {
        if let Some(r) = self.regions.get_mut(id as usize) { r.maps += 1; }
    }

    /// The last capability to the region vanished: it stops accepting new maps (nobody
    /// can ask anyway); the frame is freed now only if nothing still maps it.
    pub fn holders_gone(&mut self, id: RegionId, fa: &mut BumpFrameAllocator) {
        if let Some(r) = self.regions.get_mut(id as usize) {
            r.alive = false;
            Self::maybe_free(r, fa);
        }
    }

    /// Called by address-space teardown for every leaf frame. Returns true if
    /// `frame` belongs to a region (so the caller must NOT free it itself).
    pub fn on_unmap(&mut self, frame: PhysFrame<Size4KiB>, fa: &mut BumpFrameAllocator) -> bool {
        match self.regions.iter_mut().find(|r| !r.freed && r.frame == frame) {
            Some(r) => {
                r.maps = r.maps.saturating_sub(1);
                Self::maybe_free(r, fa);
                true
            }
            None => false,
        }
    }

    /// Final sweep after all spaces are gone: frees regions that were never
    /// mapped (or whose owner never died). Returns how many it had to free.
    pub fn release_all(&mut self, fa: &mut BumpFrameAllocator) -> usize {
        let mut n = 0;
        for r in self.regions.iter_mut().filter(|r| !r.freed) {
            r.alive = false;
            r.maps = 0;
            Self::maybe_free(r, fa);
            n += 1;
        }
        n
    }

    fn maybe_free(r: &mut Region, fa: &mut BumpFrameAllocator) {
        if !r.alive && r.maps == 0 && !r.freed {
            r.freed = true;
            unsafe { fa.deallocate_frame(r.frame) };
        }
    }
}

/// Kernel self-test of the lifecycle and of kernel-side zeroing. Needs the
/// real frame allocator; runs before the demo processes exist.
pub fn run(fa: &mut BumpFrameAllocator) -> bool {
    use crate::serial_println;
    let mut ok = true;
    let mut check = |name: &str, c: bool| { if !c { serial_println!("  experimental shm: CHECK FAILED: {}", name); ok = false; } };

    // 1. zeroing: poison a frame, free it, and make sure the region that
    //    reuses it (free list is LIFO) reads back all-zero.
    let poison = fa.allocate_frame().expect("frame");
    unsafe { core::ptr::write_bytes(poison.start_address().as_u64() as *mut u8, 0xAA, PAGE) };
    unsafe { fa.deallocate_frame(poison) };
    let mut t = ShmTable::new();
    let r0 = t.create(fa).unwrap();
    let f0 = t.frame_for_map(r0).unwrap();
    check("recycled frame was reused", f0 == poison);
    let zeroed = (0..PAGE).all(|i| unsafe { *((f0.start_address().as_u64() + i as u64) as *const u8) } == 0);
    check("recycled frame zeroed by kernel", zeroed);

    // 2. lifecycle: two mappings, owner dies, frame survives until the last unmap
    let in_use = fa.frames_in_use();
    t.note_mapped(r0); t.note_mapped(r0);
    t.holders_gone(r0, fa);
    check("dead region refuses new maps", t.frame_for_map(r0) == Err(ShmError::Dead));
    check("frame kept while mapped", fa.frames_in_use() == in_use);
    check("first unmap recognised as shared", t.on_unmap(f0, fa));
    check("frame kept after first unmap", fa.frames_in_use() == in_use);
    check("second unmap recognised", t.on_unmap(f0, fa));
    check("frame freed after last unmap", fa.frames_in_use() == in_use - 1);
    check("freed frame no longer claimed", !t.on_unmap(f0, fa));
    check("bad region id", t.frame_for_map(99) == Err(ShmError::BadRegion));

    // 3. region never mapped, last holder gone -> freed immediately
    let r1 = t.create(fa).unwrap();
    let before = fa.frames_in_use();
    t.holders_gone(r1, fa);
    check("unmapped region freed when holders are gone", fa.frames_in_use() == before - 1);
    let _ = r1;
    serial_println!("  experimental shm self-test: kernel zeroes recycled frames, holders-gone/refcounted lifecycle, no double free -- {}", if ok { "OK" } else { "FAILED" });
    ok
}
