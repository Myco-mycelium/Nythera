//! Surface windows for boots that do not run the 0x84 proof.
//!
//! The proof boot installs its own region mapper (`svc84_proof::
//! allocate_region_mapping`), with its own window range, its own frame
//! accounting and its own mirror into the peer's space -- and every number
//! the proof's verdict checks is a fact about THAT allocator. The ordinary
//! boot installed nothing, so `DISPLAY_SURFACE` (and any other region
//! request) was refused `-ENOSYS` there: a display server could hand out
//! authority but not pixels.
//!
//! This module is the general half. It installs the same three hooks
//! `caps` expects -- map, release, unmap -- over the boot's own frame
//! allocator, at a window range of its own, and it mirrors each window into
//! the address space of the DISPLAY SERVER (looked up through
//! `space_registry`) when that space is known and is not the one already
//! being mapped. That mirror is the cross-address-space window: the
//! compositing server reads a client's surface through a window that exists
//! in ITS page tables, over the same frames, with no copy and no CR3
//! switch.
//!
//! Two independent tables, because the two consumers ask for windows at
//! different rates and for different reasons:
//!
//! * `GENERAL` -- the `caps` hooks above. A region request (the 0x4f leg,
//!   any transferable shared memory) is a deliberate act, and on the proof
//!   boot these hooks are the proof's own monotonic table, untouched here.
//! * `SURFACE` -- the display's own windows, reached through
//!   [`map_surface`]/[`release_surface`] rather than through `caps`' hooks,
//!   so that a display server does not spend the same VA budget as the
//!   capability machine. A client opens a surface, draws, composes, and
//!   gives it back -- many times over a boot -- so this table is RECLAIMED:
//!   a window's base is derived from its slot, releasing the window clears
//!   the slot, and the base becomes available again. Eight is a bound on
//!   concurrent surfaces, not on how many a boot may open.
//!
//! Which is the whole reason the tables are separate: the surface table is
//! installed on the ORDINARY boot and on the PROOF boot alike
//! ([`install_surface_allocator`]), while the general table is replaced by
//! the proof's monotonic one when the proof is requested. A churn of
//! surfaces therefore measures the surface table, not the proof's pinned
//! window bases.
//!
//! Each slot also remembers the space its mirror was placed in, so a release
//! removes the mirror from the space that actually has it rather than from
//! whatever the server's space happens to be later.
//!
//! Honest limits: a slot is reclaimed by the region RELEASER only. A holder
//! that drops just its own window (`unmap_window`) does not release frames,
//! so it does not release the slot -- the slot outlives the last holder's
//! mapping and returns when the region itself does. And the mirror only
//! exists when the server's session was registered and runs in a different
//! space; both are refusals, not silent half-mappings.

use core::sync::atomic::{AtomicU64, Ordering};

use x86_64::structures::paging::{
    FrameAllocator, FrameDeallocator, Mapper, OffsetPageTable, Page, PageTable, PageTableFlags,
    PhysFrame, Size4KiB,
};
use x86_64::VirtAddr;

use crate::experimental::caps::{self, Region};
use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::memory::paging;

/// Where the general windows live: a user-half range of its own, clear of
/// the proof's windows (which sit just above its code page) and of every
/// loader base the layout gates pin.
pub const WINDOW_BASE: u64 = 0x4000_0000_0000;
/// Where the SURFACE windows live: the same region, far enough along that a
/// full general table can never reach it (its whole budget is a couple of
/// MiB), and equally clear of everything the layout gates pin.
pub const SURFACE_WINDOW_BASE: u64 = WINDOW_BASE + 0x2000_0000;
/// One window per slot, spaced by the largest window a request may ask for
/// ([`MAX_PAGES`]) so two live windows can never overlap no matter how many
/// pages each one claimed.
pub const WINDOW_STRIDE: u64 = MAX_PAGES * 4096;
/// How many windows a table will ever have live at once. Releasing a window
/// makes its slot available again, so this is a bound on CONCURRENT windows,
/// not on how many a boot may open over its lifetime.
pub const SLOTS: u64 = 8;
/// The largest request this module will map into one window. It matches the
/// bound `caps` enforces, and [`WINDOW_STRIDE`] is derived from it.
pub const MAX_PAGES: u64 = 64;

const SLOT_MASK: u64 = if SLOTS >= 64 {
    u64::MAX
} else {
    (1u64 << SLOTS) - 1
};

static ALLOCATOR: AtomicU64 = AtomicU64::new(0);

/// One reclaiming table of windows: the range it hands out of, which slots
/// are live, the space each live slot's mirror went into, and how many
/// windows it has ever issued.
struct SlotTable {
    base: u64,
    /// One bit per slot: set means live. Taken and released with
    /// compare-and-swap so two sessions asking at once cannot be given the
    /// same window.
    used: AtomicU64,
    /// The space each live slot's mirror was placed in (0 = no mirror). Kept
    /// per slot, because a release must remove the mirror from the space
    /// that has it, not from the space the display server has since moved
    /// to.
    mirrors: [AtomicU64; SLOTS as usize],
    /// Windows handed out since boot (monotonic, for the boot transcript and
    /// for a caller that wants to see churn in the issue count while the
    /// live count stays flat).
    issued: AtomicU64,
}

impl SlotTable {
    const fn new(base: u64) -> Self {
        Self {
            base,
            used: AtomicU64::new(0),
            mirrors: [const { AtomicU64::new(0) }; SLOTS as usize],
            issued: AtomicU64::new(0),
        }
    }

    /// Claims the lowest free slot, or `None` when every slot is live.
    fn take(&self) -> Option<u64> {
        loop {
            let used = self.used.load(Ordering::Acquire);
            let free = !used & SLOT_MASK;
            if free == 0 {
                return None;
            }
            let slot = free.trailing_zeros() as u64;
            if self
                .used
                .compare_exchange(used, used | (1u64 << slot), Ordering::AcqRel, Ordering::Acquire)
                .is_ok()
            {
                self.mirrors[slot as usize].store(0, Ordering::Release);
                self.issued.fetch_add(1, Ordering::SeqCst);
                return Some(slot);
            }
        }
    }

    /// Returns a slot to the table. Idempotent: clearing a clear bit is a
    /// no-op, and the mirror record is cleared either way, so a release that
    /// arrives twice cannot resurrect a window.
    fn free(&self, slot: u64) {
        if slot >= SLOTS {
            return;
        }
        self.mirrors[slot as usize].store(0, Ordering::Release);
        self.used.fetch_and(!(1u64 << slot), Ordering::AcqRel);
    }

    /// Which slot a window base belongs to, if it is one of this table's.
    fn slot_of(&self, base: u64) -> Option<u64> {
        if base < self.base {
            return None;
        }
        let offset = base - self.base;
        if offset % WINDOW_STRIDE != 0 {
            return None;
        }
        let slot = offset / WINDOW_STRIDE;
        if slot >= SLOTS {
            None
        } else {
            Some(slot)
        }
    }

    fn in_use(&self) -> u64 {
        (self.used.load(Ordering::SeqCst) & SLOT_MASK).count_ones() as u64
    }

    fn issued(&self) -> u64 {
        self.issued.load(Ordering::SeqCst)
    }

    fn mirror_of(&self, slot: u64) -> Option<u64> {
        if slot >= SLOTS {
            return None;
        }
        let pml4 = self.mirrors[slot as usize].load(Ordering::Acquire);
        if pml4 == 0 {
            None
        } else {
            Some(pml4)
        }
    }

    /// Exercises the table's own rules, at boot, before anything can ask for
    /// a window: every slot can be taken once, an exhausted table refuses
    /// rather than overlapping, releasing gives a slot back, and the FIRST
    /// slot comes back first, so reuse is lowest-free rather than unbounded
    /// growth. Leaves the table empty.
    fn self_test(&self) -> bool {
        if self.in_use() != 0 {
            return false;
        }
        let mut taken = 0;
        while self.take().is_some() {
            taken += 1;
        }
        let all_taken = taken == SLOTS && self.in_use() == SLOTS;
        // The next request must be REFUSED, not given a window that overlaps.
        let refused_when_full = self.take().is_none();
        // Free the odd slots, keep the even ones: reuse must be exact.
        for slot in (0..SLOTS).step_by(2) {
            self.free(slot);
        }
        let freed = self.in_use() == SLOTS / 2;
        let mut reused = true;
        for expected in (0..SLOTS).step_by(2) {
            match self.take() {
                Some(slot) if slot == expected => {}
                _ => reused = false,
            }
        }
        // A base that is not on a slot boundary is nobody's window, and a
        // base inside the range but past the last slot is refused as well.
        let geometry_ok = self.slot_of(self.base) == Some(0)
            && self.slot_of(self.base + WINDOW_STRIDE) == Some(1)
            && self.slot_of(self.base + WINDOW_STRIDE + 4096).is_none()
            && self.slot_of(self.base + SLOTS * WINDOW_STRIDE).is_none()
            && self.slot_of(self.base - 4096).is_none();
        for slot in 0..SLOTS {
            self.free(slot);
        }
        all_taken && refused_when_full && freed && reused && geometry_ok && self.in_use() == 0
    }
}

static GENERAL: SlotTable = SlotTable::new(WINDOW_BASE);
static SURFACE: SlotTable = SlotTable::new(SURFACE_WINDOW_BASE);

fn allocator() -> Option<&'static mut BumpFrameAllocator> {
    let address = ALLOCATOR.load(Ordering::SeqCst) as *mut BumpFrameAllocator;
    if address.is_null() {
        return None;
    }
    Some(unsafe { &mut *address })
}

/// Installs the general hooks on the boot's frame allocator, and arms the
/// surface table with the same allocator. Returns false (and installs
/// nothing) when the allocator pointer is null.
///
/// # Safety
/// `frame_allocator` must outlive the boot. It is the kernel's own bump
/// allocator, taken by raw pointer the same way the proof boot takes it, so
/// the caller must not hand it to anything that would move or drop it.
pub unsafe fn install(frame_allocator: *mut BumpFrameAllocator) -> bool {
    if frame_allocator.is_null() {
        return false;
    }
    ALLOCATOR.store(frame_allocator as u64, Ordering::SeqCst);
    unsafe {
        caps::install_region_hooks(window_mapping, release_window, unmap_window);
    }
    true
}

/// Arms ONLY the surface table. This is the proof boot's form: its general
/// hooks are the proof's own (monotonic, and pinned by the verdict), while
/// the display's windows still need a reclaimed budget and the same
/// allocator, so the frame accounting covers them.
///
/// # Safety
/// Same contract as [`install`].
pub unsafe fn install_surface_allocator(frame_allocator: *mut BumpFrameAllocator) -> bool {
    if frame_allocator.is_null() {
        return false;
    }
    ALLOCATOR.store(frame_allocator as u64, Ordering::SeqCst);
    true
}

/// How many surface windows are live right now.
pub fn surface_in_use() -> u64 {
    SURFACE.in_use()
}

/// How many frames the SURFACE table is currently holding: the windows' own
/// pages PLUS the page tables its mappings brought into existence (tables are
/// never given back, so a consumed-then-released window still costs its
/// tables). The proof's region legs measure the kernel allocator against a
/// baseline captured before their pump, and a surface that is alive across
/// that pump -- the peer's tile is, by design -- would otherwise look like a
/// leak of the region machine's own. Naming the exact number keeps that check
/// precise instead of loosening it.
static SURFACE_FRAMES_HELD: AtomicU64 = AtomicU64::new(0);

/// Frames the surface table is holding right now, in the shared allocator.
pub fn surface_frames_held() -> u64 {
    SURFACE_FRAMES_HELD.load(Ordering::SeqCst)
}

/// How many surface windows have been handed out since boot. A churn that
/// releases as it goes makes this climb while [`surface_in_use`] stays put,
/// which is the pair a caller needs to see reclamation rather than growth.
pub fn surface_issued() -> u64 {
    SURFACE.issued()
}

/// How many general windows are live right now.
pub fn windows_in_use() -> u64 {
    GENERAL.in_use()
}

/// How many general windows have been handed out since boot.
pub fn windows_issued() -> u64 {
    GENERAL.issued()
}

/// The tables' own rules, both of them, before anything can ask for a
/// window.
pub fn slot_self_test() -> bool {
    GENERAL.self_test() && SURFACE.self_test()
}

// ---- mapping -----------------------------------------------------------

fn window_flags() -> PageTableFlags {
    PageTableFlags::PRESENT | PageTableFlags::WRITABLE | PageTableFlags::USER_ACCESSIBLE
        | PageTableFlags::NO_EXECUTE
}

/// An `OffsetPageTable` over an arbitrary PML4. Safe to build for an
/// inactive space: page tables live in the identity-mapped low 1 GiB, so
/// writing their entries does not need that space active.
unsafe fn mapper_for_pml4(pml4: u64) -> OffsetPageTable<'static> {
    let table: &'static mut PageTable = unsafe { &mut *(pml4 as *mut PageTable) };
    unsafe { OffsetPageTable::new(table, VirtAddr::new(0)) }
}

fn active_pml4() -> u64 {
    let (frame, _flags) = x86_64::registers::control::Cr3::read();
    frame.start_address().as_u64()
}

/// The space the display server runs in, when it is registered and is not
/// the space that is active already. `None` means "nothing to mirror into".
fn server_pml4() -> Option<u64> {
    let server = crate::display::server();
    if server == crate::display::NO_SERVER {
        return None;
    }
    let pml4 = crate::experimental::space_registry::pml4_of(server)?;
    if pml4 == active_pml4() {
        None
    } else {
        Some(pml4)
    }
}

/// Maps `pages` fresh zeroed frames at a window of `table` in the ACTIVE
/// space, and mirrors the same frames at the same VA into the display
/// server's space when one is registered. All-or-nothing: a partial map or a
/// refused mirror is unrolled before the refusal, so a region's geometry
/// never lies about what is mapped -- and the slot is returned, so a failed
/// request does not consume a window.
unsafe fn map_window(table: &'static SlotTable, pages: u64) -> Option<Region> {
    if pages == 0 || pages > MAX_PAGES {
        return None;
    }
    let Some(slot) = table.take() else {
        return None;
    };
    let base = table.base + slot * WINDOW_STRIDE;
    // What the mapping costs the shared allocator: the window's pages AND any
    // page table it had to create. Measured, not assumed -- `map_to`'s table
    // allocation is the allocator's business.
    let tracks_frames = core::ptr::eq(table, &SURFACE);
    let before = allocator().map(|a| a.frames_in_use()).unwrap_or(0);
    let result = unsafe { map_window_at(table, slot, base, pages) };
    if tracks_frames {
        if let Some(after) = allocator().map(|a| a.frames_in_use()) {
            SURFACE_FRAMES_HELD.fetch_add(after.saturating_sub(before), Ordering::SeqCst);
        }
    }
    match result {
        Some(region) => Some(region),
        None => {
            table.free(slot);
            None
        }
    }
}

/// The body of [`map_window`] once a slot is held: map, zero, mirror.
unsafe fn map_window_at(
    table: &'static SlotTable,
    slot: u64,
    base: u64,
    pages: u64,
) -> Option<Region> {
    let mut allocator = allocator()?;
    let mut mapper = unsafe { crate::experimental::address_space::active_mapper() };
    let flags = window_flags();
    let mut mapped: u64 = 0;
    for index in 0..pages {
        let Some(frame) = allocator.allocate_frame() else {
            break;
        };
        let page: Page<Size4KiB> = Page::containing_address(VirtAddr::new(base + index * 4096));
        match unsafe { mapper.map_to(page, frame, flags, &mut *allocator) } {
            Ok(flush) => {
                flush.flush();
                mapped += 1;
            }
            Err(_) => {
                allocator.deallocate_frame(frame);
                break;
            }
        }
    }
    if mapped != pages {
        unsafe { unmap_owned(base, mapped) };
        return None;
    }
    // A fresh page is not guaranteed zero, and a surface a client can read
    // must not expose whatever the frame held before.
    unsafe { core::ptr::write_bytes(base as *mut u8, 0, (pages * 4096) as usize) };
    // The mirror: same frames, same address, the server's page tables.
    if let Some(pml4) = server_pml4() {
        if !unsafe { mirror_window(&mapper, pml4, base, pages, &mut *allocator) } {
            unsafe {
                unmap_owned(base, pages);
                unmap_peer(pml4, base, pages);
            }
            return None;
        }
        table.mirrors[slot as usize].store(pml4, Ordering::Release);
    }
    Some(Region {
        base,
        len: pages * 4096,
    })
}

/// Places the window's own frames in another space, page by page, at the
/// same virtual address. Returns false if any page cannot be read back from
/// the active space or mapped into the other one; the caller unrolls.
unsafe fn mirror_window(
    source: &OffsetPageTable<'static>,
    pml4: u64,
    base: u64,
    pages: u64,
    allocator: &mut BumpFrameAllocator,
) -> bool {
    let mut server = unsafe { mapper_for_pml4(pml4) };
    let flags = window_flags();
    for index in 0..pages {
        let va = VirtAddr::new(base + index * 4096);
        let Some(phys) = paging::translate(source, va) else {
            return false;
        };
        let frame = PhysFrame::<Size4KiB>::containing_address(phys);
        let page: Page<Size4KiB> = Page::containing_address(va);
        match unsafe { server.map_to(page, frame, flags, allocator) } {
            Ok(flush) => flush.flush(),
            Err(_) => return false,
        }
    }
    true
}

/// Unmaps `pages` at `base` in the ACTIVE space and hands the frames back.
unsafe fn unmap_owned(base: u64, pages: u64) {
    let Some(allocator) = allocator() else {
        return;
    };
    let mut mapper = unsafe { crate::experimental::address_space::active_mapper() };
    for index in 0..pages {
        let page: Page<Size4KiB> = Page::containing_address(VirtAddr::new(base + index * 4096));
        if let Ok((frame, flush)) = mapper.unmap(page) {
            flush.flush();
            allocator.deallocate_frame(frame);
        }
    }
}

/// Removes the mirrored pages from the server's space (no frame is freed:
/// they belong to the window this module owns).
unsafe fn unmap_peer(pml4: u64, base: u64, pages: u64) {
    let mut server = unsafe { mapper_for_pml4(pml4) };
    for index in 0..pages {
        let page: Page<Size4KiB> = Page::containing_address(VirtAddr::new(base + index * 4096));
        if let Ok((_frame, flush)) = server.unmap(page) {
            flush.flush();
        }
    }
}

/// Gives one window back: unmap it in the active space, remove its mirror
/// from the space that recorded it, and return its slot.
unsafe fn release_window_of(table: &'static SlotTable, geometry: Region) {
    let pages = geometry.len / 4096;
    let tracks_frames = core::ptr::eq(table, &SURFACE);
    let slot = table.slot_of(geometry.base);
    let recorded = slot.and_then(|slot| table.mirror_of(slot));
    unsafe {
        unmap_owned(geometry.base, pages);
        // The recorded space is authoritative: if the display server has
        // withdrawn or moved since the window was made, its old mirror is
        // still the one that has to go.
        let mirror = match (slot, recorded) {
            (Some(_), Some(pml4)) => Some(pml4),
            // A window of ours with no mirror recorded had none placed.
            (Some(_), None) => None,
            // Not a window of ours: nothing was mirrored on our behalf, but
            // fall back to the server's space so a stale mirror cannot
            // survive a region we did not create.
            (None, _) => server_pml4(),
        };
        if let Some(pml4) = mirror {
            unmap_peer(pml4, geometry.base, pages);
        }
    }
    if let Some(slot) = slot {
        table.free(slot);
    }
    if tracks_frames {
        // The window's own pages go back; the page tables that carried them
        // do not (nothing in this kernel frees a table), so only the pages
        // leave the held count.
        SURFACE_FRAMES_HELD.fetch_sub(pages.min(surface_frames_held()), Ordering::SeqCst);
    }
}

/// Unmaps one holder's window without freeing anything: the frames and every
/// other holder's window stay, and so does the slot, because the region it
/// names is still alive.
unsafe fn unmap_single_window(geometry: Region) {
    let mut mapper = unsafe { crate::experimental::address_space::active_mapper() };
    for index in 0..geometry.len / 4096 {
        let page: Page<Size4KiB> =
            Page::containing_address(VirtAddr::new(geometry.base + index * 4096));
        if let Ok((_frame, flush)) = mapper.unmap(page) {
            flush.flush();
        }
    }
}

/// The `caps` mapper: the general table's next window.
unsafe fn window_mapping(pages: u64) -> Option<Region> {
    unsafe { map_window(&GENERAL, pages) }
}

/// The `caps` releaser: the last capability over a requested region went
/// away, so its window and its frames go too -- and the slot goes back to
/// the table, window base and all.
unsafe fn release_window(geometry: Region) {
    unsafe { release_window_of(&GENERAL, geometry) }
}

/// The `caps` per-holder unmapper: one holder drops ITS window, the frames
/// and every other holder's window stay.
unsafe fn unmap_window(geometry: Region) {
    unsafe { unmap_single_window(geometry) }
}

// ---- the display's own windows -----------------------------------------

/// A surface window for the calling client: one page (a 32x32 tile of
/// 32-bit words), zeroed, mirrored into the display server's space. The
/// caller owns it until [`release_surface`].
///
/// # Safety
/// Must be called from the trap path of the client that will hold the
/// window (the mapping lands in the ACTIVE space).
pub unsafe fn map_surface(pages: u64) -> Option<Region> {
    unsafe { map_window(&SURFACE, pages) }
}

/// Gives a surface window back: unmaps it, frees its frames, removes its
/// mirror and returns its slot, so the next surface can use the same base.
///
/// # Safety
/// `geometry` must be a region [`map_surface`] answered, released from a
/// space that still maps it.
pub unsafe fn release_surface(geometry: Region) {
    unsafe { release_window_of(&SURFACE, geometry) }
}
