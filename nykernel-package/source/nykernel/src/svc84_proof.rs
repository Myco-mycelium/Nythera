//! The ring-3 proof of the `0x84` gate: a real user program calls it.
//!
//! Everything else about `0x84` in this tree is kernel-side: a dispatcher, a
//! trap entry, a boot self-test that calls `dispatch` from ring 0. That
//! leaves the one question a service surface actually has to answer --
//! can a ring-3 program call it and get its answers back? -- unproven. This
//! module answers it by loading `userprog/prog_svc84.s` into its own address
//! space, entering ring 3, and letting the program itself drive the calls;
//! `service84::CALL_PROOF_DONE` then reads the results back out of the
//! program's memory and compares them with what the kernel returned.
//!
//! Boot mode, not default behaviour: this runs only when the loader's
//! command line carries `nyrqis.proof=svc84`, and it does not return (the
//! proof ends with the gate reading the verdict off the serial console, the
//! same way the kernel's other ring-3 proofs end). A normal boot is
//! untouched, which is the point of making it a mode: the proof needs a
//! machine state -- a bound framebuffer, a bound pointer -- that the ordinary
//! boot has, and a gate that silently changed the default boot to get it
//! would be the wrong trade.
//!
//! Addresses are fixed and must match `prog_svc84.s`:
//!   code  0x6_0000_00000 (userprog/linker_svc84.ld)
//!   buf   0x6_0000_01000 (the results page)
//!   stack 0x6_0000_02000

use x86_64::registers::control::{Cr3, Cr3Flags};
use x86_64::structures::paging::{
    FrameDeallocator, Mapper, OffsetPageTable, Page, PageTable, PageTableFlags, Size4KiB,
};
use x86_64::{PhysAddr, VirtAddr};

use core::sync::atomic::{AtomicU32, AtomicU64, Ordering};

use crate::experimental::address_space::AddressSpace;
use crate::experimental::{elf, usermode};
use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::memory::paging;
use crate::serial_println;

/// The program's load address (userprog/linker_svc84.ld): 6 << 40, which is
/// how the linker script spells `0x60000000000`. Written as a shift so it
/// cannot be one digit short -- the first version of this constant was.
pub const CODE: u64 = 0x6u64 << 40;
/// The results page the program writes into and the kernel reads back.
/// Derived from [`CODE`] exactly as `prog_svc84.s` derives its `BUF` from
/// the same anchor -- a hand-copied page address is a digit-count bug
/// waiting to happen, and the first version of this proof had one (the
/// program wrote a page below what the kernel had mapped and faulted).
pub const PROOF_BUF: u64 = CODE + 0x1_0000;
/// The program's stack page.
pub const PROOF_STACK: u64 = CODE + 0x2_0000;
/// The SHARED REGION the region capability names: one page, mapped
/// USER_ACCESSIBLE, written by the kernel here and by ring 3 later. Both
/// sides read the SAME frames, which is what makes the SharedMem kind's
/// proof a two-party one rather than a self-check.
pub const PROOF_SHM: u64 = CODE + 0x3_0000;
/// What the kernel writes into the region before ring 3 runs: the value
/// the program must read back THROUGH the mapping.
pub const SHM_KERNEL_SENTINEL: u64 = 0x0123_4567_89ab_cdef;
/// What the program writes back; the kernel reads it out of the region
/// after the proof closes.
pub const SHM_RING3_VALUE: u64 = 0xfeed_face_dead_beef;

/// The window a REQUESTED region is mapped at (0x4f `CAP_SHM_MAP`): the
/// kernel picks the base, the caller is only told it. One 64 KiB slot per
/// request, so a request can never overlap the one before it and the
/// largest legal region (16 pages) still leaves the next slot's first
/// page untouched.
pub const SHM_REQUEST_WINDOW: u64 = CODE + 0x10_0000;
pub const SHM_REQUEST_STRIDE: u64 = 0x1_0000;
/// How many slots the proof boot's window has. Smaller than the region
/// table's own capacity on purpose: the window is a fixed VA budget, and
/// a proof that exhausted it would be measuring the budget, not the row.
const MAX_REQUEST_SLOTS: u64 = 8;
/// What the ring-3 process writes through the REQUESTED region's first
/// word. The kernel's releaser reads that word back out of the mapping
/// BEFORE unmapping it, so "the holder wrote, the kernel read" is the same
/// two-party evidence the kernel-mapped SHM leg carries.
pub const SHM_REQUEST_VALUE: u64 = 0xdead_beef_cafe_babe;
/// What the first process writes into the region it ASKS for and then HANDS
/// ON through the transfer field (0x47). It writes it BEFORE giving its own
/// capability back, and the PEER reads it through the same mapping AFTER
/// that close -- which is what makes the transferred region's lifetime
/// observable from both sides: the pages outlived the sender's capability.
pub const SHM_TRANSFER_VALUE: u64 = 0xfeed_face_cafe_d00d;
/// What the RECEIVING process writes at offset 8 of that region. The
/// releaser reads the last region's first TWO words out of the mapping
/// before unmapping it, so a value only the receiver wrote is in the
/// kernel's own record: the same two-party evidence the kernel-mapped leg
/// carries, at the moment the pages actually go back.
pub const SHM_TRANSFER_WRITE: u64 = 0x1122_3344_5566_7788;

/// The raw allocator the region-request hooks map and free through. The
/// coordinator owns the allocator by `&mut`; the hooks run inside a trap
/// taken while that coordinator is suspended in `yield_task`, which is the
/// same hand-off shape every other demo in this tree uses (a `static mut
/// Option<BumpFrameAllocator>` there, a raw pointer here because `run`
/// must keep reading the allocator's counters afterwards). Nothing else
/// touches the allocator while a request is in flight.
static REGION_ALLOCATOR: AtomicU64 = AtomicU64::new(0);
/// Next free window slot (monotonic).
static REGION_SLOT: AtomicU64 = AtomicU64::new(0);
/// The frame count just before the pump: the requested regions are all
/// created and destroyed inside it, so the count must come back to this.
static REGION_FRAMES_BASELINE: AtomicU64 = AtomicU64::new(u64::MAX);
/// What the releaser saw, in release order: how many regions came back,
/// the first and last one's first word (written by the holder), and their
/// bases.
static REGION_RELEASED: AtomicU64 = AtomicU64::new(0);
/// Releases that found no window in the releasing space (the holder had
/// dropped it with 0x50). One is expected: the window leg's own region.
static REGION_RELEASED_UNMAPPED: AtomicU64 = AtomicU64::new(0);
static REGION_FIRST_WORD: AtomicU64 = AtomicU64::new(0);
static REGION_LAST_WORD: AtomicU64 = AtomicU64::new(0);
/// The LAST released region's second word: what the RECEIVER of a
/// transferred region wrote there while it held the mapping.
static REGION_LAST_WORD8: AtomicU64 = AtomicU64::new(0);
/// The FIRST released region's second word. The region that is HANDED ON
/// is the first one released by construction: the receiving process gives
/// its capability back BEFORE it answers the call that unblocks the
/// sender, and the sender's own two requests are made after that answer
/// (its step 15). So the first release is the transfer leg's, and this is
/// the receiver's write read out of the mapping at that moment.
static REGION_FIRST_WORD8: AtomicU64 = AtomicU64::new(0);
static REGION_FIRST_BASE: AtomicU64 = AtomicU64::new(0);
static REGION_LAST_BASE: AtomicU64 = AtomicU64::new(0);

/// The REVOCATION leg's own record: the base the kernel took back, whether
/// the machine accepted the revoke, whether the wake reached the parked
/// holder, and which task the coordinator expected to fault. The fault facts
/// come from `experimental::user_fault` at read-back time: since the policy
/// landed, no arm step is needed -- a ring-3 fault is recorded and kills only
/// that task, which is what makes the holder's own read the evidence.
static REVOKE_BASE: AtomicU64 = AtomicU64::new(0);
static REVOKE_OK: AtomicU64 = AtomicU64::new(0);
static REVOKE_WOKE: AtomicU64 = AtomicU64::new(0);
static REVOKE_TASK: AtomicU64 = AtomicU64::new(u64::MAX);
/// The seq the coordinator wakes the parked holder with.
const REVOKE_SEQ: u32 = 99;

/// The two proof processes' address spaces: each task's PML4 physical
/// address and the top of that space's private RSP0 stack. Zero until the
/// proof builds them (the ordinary boot never does), so every reader can
/// tell "there is only one space" from "there are two".
static PROOF_PML4: AtomicU64 = AtomicU64::new(0);
static PROOF_RSP0: AtomicU64 = AtomicU64::new(0);
static PEER_PML4: AtomicU64 = AtomicU64::new(0);
static PEER_RSP0: AtomicU64 = AtomicU64::new(0);

/// Switches the CPU to the PEER process's address space. Used by the
/// coordinator's read-back: the peer's results page is the SAME VIRTUAL
/// ADDRESS as the first program's, so the page read is only meaningful
/// with the right CR3.
///
/// # Safety
/// Only call from the coordinator (task 0) after both ring-3 tasks are
/// gone, or from a trap that will switch back before returning to ring 3.
/// The kernel half is shared by both spaces, so the code doing this and
/// its stack stay mapped either way.
pub unsafe fn select_peer_space() {
    let pml4 = PEER_PML4.load(Ordering::SeqCst);
    if pml4 != 0 {
        Cr3::write(
            x86_64::structures::paging::PhysFrame::containing_address(PhysAddr::new(pml4)),
            Cr3Flags::empty(),
        );
        crate::gdt::set_rsp0(VirtAddr::new(PEER_RSP0.load(Ordering::SeqCst)));
    }
}

/// Switches back to the FIRST program's address space (the one the
/// coordinator runs in, and the one every other read-back assumes).
///
/// # Safety
/// Same contract as [`select_peer_space`].
pub unsafe fn select_proof_space() {
    let pml4 = PROOF_PML4.load(Ordering::SeqCst);
    if pml4 != 0 {
        Cr3::write(
            x86_64::structures::paging::PhysFrame::containing_address(PhysAddr::new(pml4)),
            Cr3Flags::empty(),
        );
        crate::gdt::set_rsp0(VirtAddr::new(PROOF_RSP0.load(Ordering::SeqCst)));
    }
}

/// The peer space's PML4, or 0 when the proof never built one. Read by
/// the region hooks (a requested region is mirrored into BOTH spaces) and
/// by the read-back.
pub fn peer_pml4() -> u64 {
    PEER_PML4.load(Ordering::SeqCst)
}

/// What the revocation leg did, for the verdict: the base the kernel took
/// back, whether the machine accepted the revoke, whether the wake reached
/// the parked holder, and -- from the armed probe -- the fault the holder's
/// own read produced.
#[derive(Clone, Copy)]
pub struct RevocationReport {
    pub base: u64,
    pub revoked: bool,
    pub woke: bool,
    pub expected_task: u64,
    pub hits: u64,
    pub fault_addr: u64,
    pub fault_error: u64,
    pub fault_task: u64,
    pub from_ring3: bool,
    /// The session the faulting task belonged to when it faulted.
    pub session: u64,
    /// `"not mapped"` or `"protection"`, from the error bits.
    pub reason: &'static str,
}

pub fn revocation_report() -> Option<RevocationReport> {
    // None on a boot that never reached the leg: `REVOKE_TASK` is only set
    // by the coordinator when it revokes.
    let expected_task = REVOKE_TASK.load(Ordering::SeqCst);
    if expected_task == u64::MAX {
        return None;
    }
    let probe = crate::experimental::user_fault::report();
    Some(RevocationReport {
        base: REVOKE_BASE.load(Ordering::SeqCst),
        revoked: REVOKE_OK.load(Ordering::SeqCst) == 1,
        woke: REVOKE_WOKE.load(Ordering::SeqCst) == 1,
        expected_task,
        hits: crate::experimental::user_fault::count(),
        fault_addr: probe.map(|p| p.addr).unwrap_or(0),
        fault_error: probe.map(|p| p.error).unwrap_or(0),
        fault_task: probe.map(|p| p.task).unwrap_or(u64::MAX),
        from_ring3: probe.map(|p| p.from_ring3).unwrap_or(false),
        session: probe.map(|p| p.session).unwrap_or(u64::MAX),
        reason: probe.map(|p| p.reason.name()).unwrap_or("none"),
    })
}

/// The kernel half of `CAP_SHM_MAP`: maps `pages` fresh frames at the next
/// free window slot, zero-fills them, and answers the geometry. `None` is
/// a real refusal (no allocator installed, window exhausted, partial
/// mapping) -- and a partial mapping is given back before answering. A
/// refused request still consumes its slot: the window is a fixed VA
/// budget handed out monotonically, and reusing a refused slot would make
/// a second request's base depend on whether the first one failed.
unsafe fn allocate_region_mapping(pages: u64) -> Option<crate::experimental::caps::Region> {
    let fa = REGION_ALLOCATOR.load(Ordering::SeqCst) as *mut BumpFrameAllocator;
    if fa.is_null() || pages == 0 {
        return None;
    }
    let slot = REGION_SLOT.fetch_add(1, Ordering::SeqCst);
    if slot >= MAX_REQUEST_SLOTS {
        return None;
    }
    let base = SHM_REQUEST_WINDOW + slot * SHM_REQUEST_STRIDE;
    let flags = PageTableFlags::PRESENT
        | PageTableFlags::WRITABLE
        | PageTableFlags::USER_ACCESSIBLE
        | PageTableFlags::NO_EXECUTE;
    let mut mapper = crate::experimental::address_space::active_mapper();
    let mapped = paging::map_range(&mut mapper, &mut *fa, VirtAddr::new(base), pages, flags);
    if mapped != pages {
        // All-or-nothing: a half-mapped window would be a region whose
        // geometry lies about its length, so the pages that DID map go
        // back before the refusal.
        for i in 0..mapped {
            let page: Page<Size4KiB> = Page::containing_address(VirtAddr::new(base + i * 4096));
            if let Ok((frame, flush)) = mapper.unmap(page) {
                flush.flush();
                (*fa).deallocate_frame(frame);
            }
        }
        return None;
    }
    // A freshly mapped page is not guaranteed zero, and a region a holder
    // can read must not expose whatever the frame held before.
    core::ptr::write_bytes(base as *mut u8, 0, (pages * 4096) as usize);
    // TWO PROCESSES, TWO ADDRESS SPACES: the requester's tables now map the
    // region, and the peer's must too, at the same VA and over the SAME
    // frames -- otherwise the capability it receives through the transfer
    // field would name a page its own space does not map. Mirrored after
    // the zero-fill so both spaces read zeroes, and all-or-nothing: a
    // failed mirror unrolls the requester's mapping rather than handing
    // back a region only one process could use.
    if !mirror_into_other_space(base, pages, flags, &mapper, &mut *fa) {
        for i in 0..pages {
            let page: Page<Size4KiB> = Page::containing_address(VirtAddr::new(base + i * 4096));
            if let Ok((frame, flush)) = mapper.unmap(page) {
                flush.flush();
                (*fa).deallocate_frame(frame);
            }
        }
        return None;
    }
    Some(crate::experimental::caps::Region { base, len: pages * 4096 })
}

/// The other half: read the holder's first word out of the mapping while
/// it is still there (the two-party fact), then unmap every page and hand
/// the frames back to the same allocator that made them.
unsafe fn release_region_mapping(geometry: crate::experimental::caps::Region) {
    let fa = REGION_ALLOCATOR.load(Ordering::SeqCst) as *mut BumpFrameAllocator;
    if fa.is_null() || geometry.len == 0 {
        return;
    }
    // The capture reads the mapping, so it is only possible while THIS
    // space maps it: since the 0x50 row landed a holder can drop its window
    // before the region dies, and the releaser running later -- still in
    // that same space -- would fault on a page nobody maps any more. The
    // guard is the machine's own record of the mapping, not a guess.
    let mapped_here = paging::page_flags_active(VirtAddr::new(geometry.base)).is_some();
    let word = if mapped_here {
        core::ptr::read_volatile(geometry.base as *const u64)
    } else {
        0
    };
    // The second word only matters for the region two PROCESSES wrote
    // (the transfer leg): the sender's marker at offset 0 is `word`, the
    // receiver's at offset 8 is this one. Read under the same "still
    // mapped" window so both are facts about the same moment.
    let word8 = if mapped_here && geometry.len >= 16 {
        core::ptr::read_volatile((geometry.base + 8) as *const u64)
    } else {
        0
    };
    if !mapped_here {
        // Released in a space that maps nothing at `base`: the holder
        // dropped its window (0x50) before the region died. Counted so the
        // verdict can say so with the machine's own record.
        REGION_RELEASED_UNMAPPED.fetch_add(1, Ordering::SeqCst);
    }
    let n = REGION_RELEASED.fetch_add(1, Ordering::SeqCst);
    if n == 0 {
        REGION_FIRST_WORD.store(word, Ordering::SeqCst);
        REGION_FIRST_WORD8.store(word8, Ordering::SeqCst);
        REGION_FIRST_BASE.store(geometry.base, Ordering::SeqCst);
    }
    REGION_LAST_WORD.store(word, Ordering::SeqCst);
    REGION_LAST_WORD8.store(word8, Ordering::SeqCst);
    REGION_LAST_BASE.store(geometry.base, Ordering::SeqCst);

    let mut mapper = crate::experimental::address_space::active_mapper();
    // The MIRROR's tables, taken before the loop so each page's frame can be
    // found from whichever space still names it: since 0x50 landed, the
    // holder that drops its window first leaves the releaser running in a
    // space that maps nothing -- and a frame freed only when the LOCAL unmap
    // succeeds would leak exactly those pages (this leaked one frame the
    // first time the window leg ran).
    let other = other_proof_pml4();
    let mut other_mapper = if other != 0 {
        Some(mapper_for_pml4(other))
    } else {
        None
    };
    for i in 0..geometry.len / 4096 {
        let page: Page<Size4KiB> =
            Page::containing_address(VirtAddr::new(geometry.base + i * 4096));
        let mut frame = None;
        if let Ok((f, flush)) = mapper.unmap(page) {
            flush.flush();
            frame = Some(f);
        }
        if let Some(peer) = other_mapper.as_mut() {
            if let Ok((f, flush)) = peer.unmap(page) {
                flush.flush();
                if frame.is_none() {
                    frame = Some(f);
                }
            }
        }
        if let Some(f) = frame {
            (*fa).deallocate_frame(f);
        }
    }
}

/// The PER-HOLDER half of the region machine's mapping source: drop ONE
/// window -- the calling holder's own space, the active one -- and nothing
/// else. The frames are NOT handed back (the region still owns them, and
/// another holder may be reading them), the mirror in the other proof space
/// stays exactly where it is, and the caller's capability survives. This is
/// what `0x50` calls, and it is the whole difference between a window and a
/// region: a close gives the capability back, a revocation takes the frames,
/// this takes one translation.
unsafe fn unmap_region_window(geometry: crate::experimental::caps::Region) {
    if geometry.len == 0 {
        return;
    }
    let mut mapper = crate::experimental::address_space::active_mapper();
    for i in 0..geometry.len / 4096 {
        let page: Page<Size4KiB> =
            Page::containing_address(VirtAddr::new(geometry.base + i * 4096));
        if let Ok((_frame, flush)) = mapper.unmap(page) {
            // Deliberately no `deallocate_frame`: the frame belongs to the
            // region, not to this window.
            flush.flush();
        }
    }
}

/// The PML4 of the proof's OTHER address space -- the one that is not
/// active right now -- or 0 when the proof built only one space (the
/// ordinary boot, or a proof that never made a peer).
///
/// The two proof processes share no user mapping except the ones the
/// region machine installs on purpose, and those have to exist in BOTH
/// spaces: whichever process REQUESTS a region maps it through its own
/// (active) tables, and the other one receives a capability naming the
/// same VA. So every install mirrors into this space, and every release
/// removes from it too -- the space active at release time is the holder's
/// that closed, not necessarily the only one that had the mapping.
unsafe fn other_proof_pml4() -> u64 {
    let (active, _) = Cr3::read();
    let active = active.start_address().as_u64();
    let a = PROOF_PML4.load(Ordering::SeqCst);
    let b = PEER_PML4.load(Ordering::SeqCst);
    if active == a {
        b
    } else if active == b {
        a
    } else {
        0
    }
}

/// Maps `pages` at `base` in the OTHER proof address space, over the
/// frames the requester's tables already name there. Returns `false` when
/// there is no other space (nothing to mirror) or when a mapping was
/// refused, in which case what it did manage is removed again.
unsafe fn mirror_into_other_space(
    base: u64,
    pages: u64,
    flags: PageTableFlags,
    requester: &OffsetPageTable<'static>,
    frame_allocator: &mut BumpFrameAllocator,
) -> bool {
    let pml4 = other_proof_pml4();
    if pml4 == 0 {
        return true;
    }
    let mut peer = mapper_for_pml4(pml4);
    let mut done = 0u64;
    while done < pages {
        let va = VirtAddr::new(base + done * 4096);
        let phys = match paging::translate(requester, va) {
            Some(p) => p,
            None => break,
        };
        let frame = x86_64::structures::paging::PhysFrame::containing_address(phys);
        let page: Page<Size4KiB> = Page::containing_address(va);
        match unsafe { peer.map_to(page, frame, flags, frame_allocator) } {
            Ok(flush) => {
                flush.flush();
                done += 1;
            }
            Err(_) => break,
        }
    }
    if done != pages {
        unmirror_from_other_space(base, done);
        return false;
    }
    true
}

/// Removes the mirror mappings for `pages` at `base` from the other proof
/// space. The frames belong to the requester's tables and are freed by the
/// caller, so they are dropped here rather than deallocated.
unsafe fn unmirror_from_other_space(base: u64, pages: u64) {
    let pml4 = other_proof_pml4();
    if pml4 == 0 {
        return;
    }
    let mut peer = mapper_for_pml4(pml4);
    for i in 0..pages {
        let page: Page<Size4KiB> =
            Page::containing_address(VirtAddr::new(base + i * 4096));
        // The flush invalidates the VA in whatever space is ACTIVE -- not
        // the peer's, which is not active here -- so it is harmless either
        // way; the unmap itself is what matters (an unreachable mapping
        // left behind would point at frames the allocator takes back).
        if let Ok((_frame, flush)) = peer.unmap(page) {
            flush.flush();
        }
    }
}

/// An `OffsetPageTable` over an arbitrary PML4 physical address. Safe to
/// build for an INACTIVE space: page tables live in the identity-mapped
/// low 1GiB, so writing their entries does not need that space active (a
/// caller that maps *user* pages through it still has to arrange for the
/// CPU to see them, which is what `activate` is for).
unsafe fn mapper_for_pml4(pml4: u64) -> OffsetPageTable<'static> {
    let table: &'static mut PageTable = &mut *(pml4 as *mut PageTable);
    OffsetPageTable::new(table, VirtAddr::new(0))
}

/// What the coordinator can read back about the REQUESTED regions after
/// both processes are gone: whether their windows are unmapped, the
/// releaser's records, and the frame count against its baseline. `None`
/// on a boot that never installed the hooks (the ordinary boot), so the
/// read-back can tell "no region was requested" from "the region was
/// requested and cleaned up".
#[derive(Clone, Copy)]
pub struct RequestedRegionReport {
    pub first_unmapped: bool,
    pub second_unmapped: bool,
    /// The window slot the HANDED-ON region was requested in: a third
    /// request, so a third slot, and it must be unmapped too.
    pub third_unmapped: bool,
    /// The FOURTH slot: the region whose window the first process dropped
    /// with the 0x50 row while it still held the capability. Its window in
    /// the first process's space must be gone, and the PEER's window over
    /// the same frames must have survived -- which the peer's own read at
    /// that address proves, not this table.
    pub fourth_unmapped: bool,
    /// How many releases found no window in the space they ran in: the
    /// window leg's region is released exactly that way (its holder dropped
    /// the window first), so this must be 1 and not 0.
    pub released_without_window: u64,

    pub released: u64,
    pub first_word: u64,
    /// The first released region's SECOND word (the receiver's write).
    pub first_word8: u64,
    pub last_word: u64,
    /// The last released region's SECOND word (the receiver's write).
    pub last_word8: u64,
    pub first_base: u64,
    pub last_base: u64,
    pub frames_in_use: u64,
    pub frames_baseline: u64,
}

pub fn requested_region_report() -> Option<RequestedRegionReport> {
    let fa = REGION_ALLOCATOR.load(Ordering::SeqCst) as *const BumpFrameAllocator;
    if fa.is_null() {
        return None;
    }
    let unmapped = |addr: u64| paging::page_flags_active(VirtAddr::new(addr)).is_none();
    // The fourth slot is the region whose window the first process DROPPED
    // (0x50) while keeping the capability. Its mirror in the peer's space is
    // checked BEFORE it goes, by the peer's own read (the proof's verdict
    // carries that value); by the time this report runs the first process's
    // exit has released its last capability, so both windows are gone and
    // the frames are back -- which is what the cleanup pin below says.
    let fourth_base = SHM_REQUEST_WINDOW + SHM_REQUEST_STRIDE;
    Some(RequestedRegionReport {
        first_unmapped: unmapped(SHM_REQUEST_WINDOW),
        second_unmapped: unmapped(SHM_REQUEST_WINDOW + SHM_REQUEST_STRIDE),
        third_unmapped: unmapped(SHM_REQUEST_WINDOW + 2 * SHM_REQUEST_STRIDE),
        fourth_unmapped: unmapped(fourth_base),
        released_without_window: REGION_RELEASED_UNMAPPED.load(Ordering::SeqCst),
        released: REGION_RELEASED.load(Ordering::SeqCst),
        first_word: REGION_FIRST_WORD.load(Ordering::SeqCst),
        first_word8: REGION_FIRST_WORD8.load(Ordering::SeqCst),
        last_word: REGION_LAST_WORD.load(Ordering::SeqCst),
        last_word8: REGION_LAST_WORD8.load(Ordering::SeqCst),
        first_base: REGION_FIRST_BASE.load(Ordering::SeqCst),
        last_base: REGION_LAST_BASE.load(Ordering::SeqCst),
        frames_in_use: unsafe { (*fa).frames_in_use() },
        frames_baseline: REGION_FRAMES_BASELINE.load(Ordering::SeqCst),
    })
}

/// The peer process's load address: THE SAME as the first program's, on
/// purpose. The two processes live in two different address spaces (each
/// task's CR3 is its own PML4), so the same VA means different frames in
/// each -- which is the claim this slice makes and the reason the anchor
/// is no longer `7 << 40`. `prog_svc84_peer.s` and
/// `userprog/linker_svc84_peer.ld` agree with these constants.
pub const PEER_CODE: u64 = CODE;
pub const PEER_BUF: u64 = PEER_CODE + 0x1_0000;
pub const PEER_STACK: u64 = PEER_CODE + 0x2_0000;

/// The peer's half of the two-process proof, written by
/// `prog_svc84_peer.s` and read back by `CALL_PROOF_DONE` after the
/// process is TERMINATED (so nothing in it can be the program answering
/// while it runs).
pub const PEER_WRITE: u64 = 0xbeef_cafe_dead_1234;

/// The session ids the two ring-3 tasks speak as. Past the boot's seven
/// scheduler task ids and the call proof's two (201/202): the id spaces
/// stay the scheduler's plus sessions', never colliding.
const PROOF_SESSION_ID: usize = 7;
const PEER_SESSION_ID: usize = 8;

/// The two ring-3 programs' entry points, recorded by [`run`] before it
/// spawns their tasks: a task entry function is a plain `fn`, so the ELF's
/// entry point reaches it through a static rather than through a spawn
/// argument the scheduler does not take.
/// The first process's session id, for the read-back's exit check.
pub const FIRST_SESSION: usize = PROOF_SESSION_ID;
/// The region id the proof created. Recorded rather than assumed: the
/// read-back asks the capability machine whether THIS region survived its
/// holders, and a table that returned some other id's answer would be a
/// different (and wrong) proof.
pub static PROOF_REGION: AtomicU32 = AtomicU32::new(u32::MAX);

static ENTRY_A: AtomicU64 = AtomicU64::new(0);
static ENTRY_B: AtomicU64 = AtomicU64::new(0);

/// The first ring-3 process's task entry. The session attach happens HERE,
/// on the task that will run the program: the identity record is the
/// scheduler's per-task slot (see `scheduler::attach_session`), so
/// attaching it on task 0 would name task 0 for both processes.
extern "C" fn ring3_a() -> ! {
    crate::service84::attach_gate_session(PROOF_SESSION_ID);
    unsafe {
        usermode::enter_with_stack(
            VirtAddr::new(ENTRY_A.load(core::sync::atomic::Ordering::SeqCst)),
            VirtAddr::new(PROOF_STACK + 4096 - 16),
        )
    }
}

/// The peer ring-3 process's task entry: same shape, its own session, its
/// own stack.
extern "C" fn ring3_b() -> ! {
    crate::service84::attach_gate_session(PEER_SESSION_ID);
    unsafe {
        usermode::enter_with_stack(
            VirtAddr::new(ENTRY_B.load(core::sync::atomic::Ordering::SeqCst)),
            VirtAddr::new(PEER_STACK + 4096 - 16),
        )
    }
}

const TOKEN: &[u8] = b"nyrqis.proof=svc84";

/// The two scancode bytes the proof drives through the REAL hardware
/// path before entering ring 3 (the INPUT_POLL row's byte source): the
/// controller is told to place each byte in its own output buffer
/// (`keyboard::inject`'s 0xD2 command), which raises IRQ1 exactly as a
/// keystroke would; the IRQ handler queues the bytes raw. The read-back
/// judges the polled records against THESE values -- the kernel's OWN
/// injected bytes, not constants typed into the program.
pub const INJECT_BYTES: [u8; 2] = [0x1E, 0x9E];

/// The separately-built ELF (see `userprog/prog_svc84.s`); built by
/// `build.sh` before the kernel crate, like the other proof programs.
static PROGRAM: &[u8] = include_bytes!("../userprog/userprog_svc84.elf");
/// The peer process, separately built and separately linked (0x7...).
static PEER_PROGRAM: &[u8] = include_bytes!("../userprog/userprog_svc84_peer.elf");

/// True when the loader's command line asks for this proof. Matched as a
/// whole token, so `nyrqis.proof=svc84x` does not silently select it.
pub fn requested(cmdline: &[u8]) -> bool {
    let mut i = 0;
    while i + TOKEN.len() <= cmdline.len() {
        if &cmdline[i..i + TOKEN.len()] == TOKEN {
            let after = cmdline.get(i + TOKEN.len()).copied();
            if matches!(after, None | Some(b' ') | Some(0)) {
                return true;
            }
        }
        i += 1;
    }
    false
}

/// Loads the proof program in a private address space, maps its results and
/// stack pages `USER_ACCESSIBLE`, and enters ring 3. Never returns.
///
/// # Safety
/// Must run once, with the framebuffer bound and its address space active.
pub unsafe fn run(frame_allocator: &mut BumpFrameAllocator) -> ! {
    let mut space = AddressSpace::new_sharing_kernel(frame_allocator)
        .unwrap_or_else(|_| panic!("svc84 proof: could not build an address space"));
    space.activate();
    let mut mapper = space.mapper();

    let loaded = elf::load(PROGRAM, &mut mapper, frame_allocator, 0, &elf::LoadContext::none())
        .unwrap_or_else(|e| panic!("svc84 proof: loading userprog_svc84.elf failed: {:?}", e));

    let flags = PageTableFlags::PRESENT
        | PageTableFlags::WRITABLE
        | PageTableFlags::USER_ACCESSIBLE
        | PageTableFlags::NO_EXECUTE;
    let buf_mapped = paging::map_range(&mut mapper, frame_allocator, VirtAddr::new(PROOF_BUF), 1, flags);
    let stack_mapped =
        paging::map_range(&mut mapper, frame_allocator, VirtAddr::new(PROOF_STACK), 1, flags);
    let shm_mapped = paging::map_range(&mut mapper, frame_allocator, VirtAddr::new(PROOF_SHM), 1, flags);
    assert_eq!(buf_mapped, 1, "svc84 proof: results page not mapped");
    assert_eq!(stack_mapped, 1, "svc84 proof: stack page not mapped");
    assert_eq!(shm_mapped, 1, "svc84 proof: shared region not mapped");
    // The kernel's half of the shared region, written through the SAME
    // mapping the program will read: there is one copy of these bytes.
    unsafe {
        core::ptr::write_volatile(PROOF_SHM as *mut u64, SHM_KERNEL_SENTINEL);
        core::ptr::write_volatile((PROOF_SHM + 8) as *mut u64, 0);
    }

    // The proof drives the IPC rows through endpoint 0 (the program's
    // SEND/RECV choice): make that endpoint exist and be EMPTY. A proof
    // boot reaches this before the ordinary ipc::init/demo phases have
    // built a table, so it may not exist at all yet; and, since the proof
    // enters ring 3 and never returns, anything some earlier phase had
    // queued there is unreachable and no one waits for it -- draining
    // loses nothing observable. See ipc::prepare_endpoint0_for_proof.
    crate::experimental::ipc::prepare_endpoint0_for_proof();
    // ...and the PEER's mailbox (endpoint 1): the second half of the
    // rendezvous, and the one the peer parks on. Creating it here rather
    // than assuming it exists is the same discipline as endpoint 0's --
    // a proof boot reaches this before the ipc demo builds a table.
    crate::experimental::ipc::prepare_endpoint_for_proof(1);

    // The install order is deterministic: `create_endpoint`'s owner cap
    // lands in slot 0 and the mint in slot 1, so the kernel-side record
    // and the program's hardcoded handle are compared against the SAME
    // granted index -- and the values are reported, so a drift fails
    // visibly rather than silently sending through some other handle.
    // The capability rows' identity is the CALLER'S OWN SESSION, recorded
    // at the transition into ring 3 (`service84::attach_gate_session` --
    // the session machine's FIRST slice): from here the program speaks
    // on the capability rows and INPUT_POLL with its OWN capability
    // table, not task 0's. The session's holder id is minted one past
    // the boot's seven scheduler task ids (0..6), so the id space stays
    // the scheduler's plus sessions', never colliding. The equip
    // (endpoint 0 owned by the session; its SEND|GRANT cap at slot 1;
    // the Input cap at slot 2) is the ONE kernel-only grant a proof boot
    // performs, explicit and terminal-path-only exactly like the ipc
    // equip above: this ring-3 program never returns, so no later phase
    // can observe the session's table; a fuller session machine
    // (the manifest's parked step) mints it at the scheduler's ring-3
    // alloc and destroys it on exit.
    // The PEER process: its own ELF, at the SAME anchor, in an address
    // space of its own. Two PML4s, two sets of user frames, identical
    // virtual addresses -- so "the same VA" no longer means "the same
    // page", and each process's writes land in its own memory. The
    // kernel half is shared (both spaces copy the kernel's PML4 entries),
    // which is what lets the two tasks trap into the same gate and keeps
    // their guarded kernel stacks visible from either space.
    //
    // The peer is LOADED with its own space active: `elf::load` writes
    // through the destination virtual addresses, so loading it under the
    // first program's tables would fill the first program's frames.
    let mut peer_space = AddressSpace::new_sharing_kernel(frame_allocator)
        .unwrap_or_else(|_| panic!("svc84 proof: could not build the peer's address space"));
    unsafe { peer_space.activate() };
    let mut peer_mapper = peer_space.mapper();
    let peer_loaded =
        elf::load(PEER_PROGRAM, &mut peer_mapper, frame_allocator, 0, &elf::LoadContext::none())
            .unwrap_or_else(|e| panic!("svc84 proof: loading userprog_svc84_peer.elf failed: {:?}", e));
    let peer_buf_mapped =
        paging::map_range(&mut peer_mapper, frame_allocator, VirtAddr::new(PEER_BUF), 1, flags);
    let peer_stack_mapped =
        paging::map_range(&mut peer_mapper, frame_allocator, VirtAddr::new(PEER_STACK), 1, flags);
    assert_eq!(peer_buf_mapped, 1, "svc84 proof: peer results page not mapped");
    assert_eq!(peer_stack_mapped, 1, "svc84 proof: peer stack page not mapped");
    // The SHARED region is the one mapping BOTH spaces must have: it is
    // the page the two processes meet in, so the peer space gets the SAME
    // frames at the same VA -- mirrored, not re-backed. Without this the
    // peer's capability would name a page that only the other space maps.
    {
        let frame = paging::translate(&mapper, VirtAddr::new(PROOF_SHM))
            .map(|p| x86_64::structures::paging::PhysFrame::<Size4KiB>::containing_address(p))
            .unwrap_or_else(|| panic!("svc84 proof: shared region frame not found"));
        unsafe {
            peer_mapper
                .map_to(Page::containing_address(VirtAddr::new(PROOF_SHM)), frame, flags, &mut *frame_allocator)
                .unwrap_or_else(|e| panic!("svc84 proof: mirroring the shared region into the peer space failed: {:?}", e))
                .flush();
        }
    }
    // Record the two spaces' PML4s for the read-back and the region hooks,
    // then put the FIRST program's space back: everything from here on --
    // the equips, the window warm-up, the hooks, the coordinator's own
    // reads -- runs in the space the program was loaded into.
    PROOF_PML4.store(
        crate::experimental::address_space::pml4_phys_addr(&space).as_u64(),
        Ordering::SeqCst,
    );
    PROOF_RSP0.store(space.kernel_stack_top(), Ordering::SeqCst);
    PEER_PML4.store(
        crate::experimental::address_space::pml4_phys_addr(&peer_space).as_u64(),
        Ordering::SeqCst,
    );
    PEER_RSP0.store(peer_space.kernel_stack_top(), Ordering::SeqCst);
    unsafe { space.activate() };

    // The two sessions' equips. The install order is asserted, not
    // assumed: the programs name these indexes, so a drift must fail HERE
    // rather than as a mysterious EBADF from ring 3.
    let (cap_ep, cap_handle, input_handle) =
        crate::experimental::caps::prepare_session_for_proof(PROOF_SESSION_ID);
    if (cap_ep, cap_handle, input_handle) != (0, 1, 2) {
        panic!(
            "svc84 proof: session equip produced endpoint {} handles {} {} -- the program names 0, 1, 2",
            cap_ep, cap_handle, input_handle
        );
    }
    let (region_id, region_handle) =
        crate::experimental::caps::prepare_region_for_proof(PROOF_SESSION_ID, PROOF_SHM, 4096);
    if region_handle != 3 {
        panic!(
            "svc84 proof: region equip produced handle {} -- the program names handle 3",
            region_handle
        );
    }
    // The peer: its own endpoint (the one the first process will park on)
    // and a SECOND grant of the same region; the first process gets a
    // second region capability plus the SEND right to the peer's endpoint.
    let (peer_ep, peer_owner, peer_region_handle, caller_region2, send_handle) =
        crate::experimental::caps::prepare_peer_for_proof(PEER_SESSION_ID, PROOF_SESSION_ID, region_id);
    if (peer_ep, peer_owner, peer_region_handle, caller_region2, send_handle) != (1, 0, 1, 4, 5) {
        panic!(
            "svc84 proof: peer equip produced endpoint {} handles {} {} {} {} -- the programs name 1, 0, 1, 4, 5",
            peer_ep, peer_owner, peer_region_handle, caller_region2, send_handle
        );
    }
    // The resource kinds go in AFTER the table's existing slots, so the
    // indexes the program already names do not move: block at 6, I/O
    // ports at 7, framebuffer at 8.
    let (block_handle, ioport_handle, fb_handle) =
        crate::experimental::caps::equip_devices_for_proof(PROOF_SESSION_ID);
    if (block_handle, ioport_handle, fb_handle) != (6, 7, 8) {
        panic!(
            "svc84 proof: device equip produced handles {} {} {} -- the program names 6, 7, 8",
            block_handle, ioport_handle, fb_handle
        );
    }
    PROOF_REGION.store(region_id, Ordering::SeqCst);
    let region_holders = crate::experimental::caps::region_holders(region_id).unwrap_or(0);
    if region_holders != 3 {
        panic!(
            "svc84 proof: the region names {} holders after the equip -- the two processes hold 3 capabilities",
            region_holders
        );
    }

    // The DISTINCT TABLES the session machine buys, asserted at the
    // kernel side, not trusted from the code's shape: task 0's holder
    // does not exist at all, so resolving a capability AS TASK 0 must
    // refuse BadProcess.
    match crate::experimental::caps::input_check_for(0, 0) {
        Err(crate::experimental::caps::CapError::BadProcess) => {}
        other => panic!(
            "svc84 proof: the gate session must NOT share task 0's table (identity task 0 answered {:?})",
            other
        ),
    }

    // The INPUT_POLL row's byte source is the REAL HARDWARE PATH, not
    // seeded bytes: the two scancodes are placed in the controller's own
    // output buffer (`keyboard::inject`, the 0xD2 command), which raises
    // IRQ1 for each byte exactly as a keystroke would; the handler
    // queues them raw. Bounded wait on the handler's own counter, with a
    // named failure -- a dead controller is a boot fact, not a hang.
    crate::experimental::input::clear();
    let scancodes_before = crate::keyboard::scancodes_received();
    for &b in &INJECT_BYTES {
        unsafe { crate::keyboard::inject(b) };
    }
    let mut spins = 0u64;
    while crate::keyboard::scancodes_received() - scancodes_before < INJECT_BYTES.len() as u64
        && spins < 50_000_000
    {
        spins += 1;
    }
    let injected = crate::keyboard::scancodes_received() - scancodes_before;
    if injected != INJECT_BYTES.len() as u64 {
        panic!(
            "svc84 proof: {} of {} injected scancodes reached IRQ1 -- the controller's output-buffer path is dead",
            injected,
            INJECT_BYTES.len()
        );
    }

    // Neither program enters ring 3 from HERE: both run as SCHEDULER
    // TASKS (spawned below), because that is the only way a ring-3 caller
    // can be PARKED on a blocking call -- task 0 has no session slot to
    // park and the boot would be left with nothing to run. The
    // coordinator stays on task 0: it spawns, pumps until both processes
    // are TERMINATED, and only then reads the results back, so nothing it
    // observes can be a process still running.
    // The scheduler is INITIALISED here, which it is not yet at this point
    // in the boot (the ordinary boot inits it later, after the demo chain;
    // this proof never returns to that code). Without this, `spawn` would
    // hand out ids 0 and 1 for a table that has no task 0 -- and id 0 is
    // the coordinator, so the first process would have been "task 0" and
    // refused its park with EPERM. That is exactly how the first run of
    // this slice failed.
    crate::experimental::scheduler::init();
    ENTRY_A.store(loaded.entry_point.as_u64(), Ordering::SeqCst);
    ENTRY_B.store(peer_loaded.entry_point.as_u64(), Ordering::SeqCst);
    // Each task names its OWN PML4, so the scheduler's `switch_to` loads the
    // right CR3 on every switch -- the two processes do not share tables
    // and cannot see each other's pages.
    let task_a = crate::experimental::scheduler::spawn_with_cr3(
        ring3_a,
        PROOF_PML4.load(Ordering::SeqCst),
        &mut mapper,
        frame_allocator,
    );
    let task_b = crate::experimental::scheduler::spawn_with_cr3(
        ring3_b,
        PEER_PML4.load(Ordering::SeqCst),
        &mut mapper,
        frame_allocator,
    );

    // The kernel half of the region REQUEST (`CAP_SHM_MAP`, 0x4f): a
    // capability table cannot conjure frames, so the boot that owns the
    // allocator installs the two hooks the row needs. The window's
    // page-table LEVELS are materialised OUTSIDE the measured baseline --
    // a first request that had to create them would look, to the frame
    // count, exactly like a region that never came back.
    unsafe {
        let flags = PageTableFlags::PRESENT
            | PageTableFlags::WRITABLE
            | PageTableFlags::USER_ACCESSIBLE
            | PageTableFlags::NO_EXECUTE;
        let mut warm = crate::experimental::address_space::active_mapper();
        let warm_base = VirtAddr::new(SHM_REQUEST_WINDOW);
        if paging::map_range(&mut warm, frame_allocator, warm_base, 1, flags) == 1 {
            let page: Page<Size4KiB> = Page::containing_address(warm_base);
            if let Ok((frame, flush)) = warm.unmap(page) {
                flush.flush();
                frame_allocator.deallocate_frame(frame);
            }
        }
        REGION_FRAMES_BASELINE.store(frame_allocator.frames_in_use(), Ordering::SeqCst);
        // The LAST use of the `&mut` before the hooks take it over: from
        // here the traps use the raw pointer, and this function only
        // reads the allocator back THROUGH that pointer.
        REGION_ALLOCATOR.store(frame_allocator as *mut BumpFrameAllocator as u64, Ordering::SeqCst);
        crate::experimental::caps::install_region_hooks(
            allocate_region_mapping,
            release_region_mapping,
            unmap_region_window,
        );
    }
    serial_println!(
        "  service-0x84 proof: region REQUESTS armed -- window {:#x} (+{:#x} per request, {} slots), {} frames in use before the pump",
        SHM_REQUEST_WINDOW,
        SHM_REQUEST_STRIDE,
        MAX_REQUEST_SLOTS,
        REGION_FRAMES_BASELINE.load(Ordering::SeqCst)
    );

    serial_println!(
        "  service-0x84 proof: program loaded at {:#x} (peer at {:#x}), results pages {:#x} and {:#x}, stacks {:#x} and {:#x} mapped USER_ACCESSIBLE; TSS.RSP0 {:#x} is {:?}; sessions {} and {} equip -- endpoint {} granted at handle {} , INPUT cap at handle {} , SHARED region {} ({:#x}..{:#x}) at handles {} and {} (two capabilities), peer endpoint {} at the peer's handle {} with the first process holding SEND at handle {} , {} region holders (task 0's table absent: distinct, verified) ; {} scancodes injected through the controller (IRQ1 -> raw ring) ; tasks {} and {} spawned -- entering ring 3",
        loaded.entry_point.as_u64(),
        peer_loaded.entry_point.as_u64(),
        PROOF_BUF,
        PEER_BUF,
        PROOF_STACK,
        PEER_STACK,
        space.kernel_stack_top(),
        paging::page_flags_active(VirtAddr::new(space.kernel_stack_top() - 8)),
        PROOF_SESSION_ID,
        PEER_SESSION_ID,
        cap_ep,
        cap_handle,
        input_handle,
        region_id,
        PROOF_SHM,
        PROOF_SHM + 4096,
        region_handle,
        caller_region2,
        peer_ep,
        peer_owner,
        send_handle,
        region_holders,
        injected,
        task_a,
        task_b
    );

    // Pump: both ring-3 tasks run until each EXITs (CAP_EXIT terminates
    // its own task). Round-robin does the ordering -- the first process
    // gets the core first, runs to its blocking call, and its park is what
    // hands the core to the peer, which answers it. Bounded, because a
    // proof that hangs is a proof that says nothing.
    let mut pumps = 0u64;
    // THE REVOCATION LEG, one-shot: once the first process has exited and
    // the peer is PARKED (its step 8), the kernel half takes the region's
    // pages back while the peer still holds the capability, then wakes it.
    // The peer's next read faults, the armed probe records it and terminates
    // that task -- so `done_b` becomes true the normal way, through the
    // scheduler's lifecycle, rather than by halting the machine.
    let mut revoked = false;
    loop {
        let done_a = matches!(
            crate::experimental::scheduler::state_of(task_a),
            Some(crate::experimental::scheduler::LifecycleState::Terminated)
        );
        let done_b = matches!(
            crate::experimental::scheduler::state_of(task_b),
            Some(crate::experimental::scheduler::LifecycleState::Terminated)
        );
        if done_a && done_b {
            break;
        }
        if !revoked
            && done_a
            && matches!(
                crate::experimental::scheduler::state_of(task_b),
                Some(crate::experimental::scheduler::LifecycleState::Blocked)
            )
        {
            revoked = true;
            // The base the kernel handed out for the handed-on region: the
            // first process recorded it at BUF+2352 when it asked (its
            // step 16a), and that is the mapping the peer holds now.
            let base = unsafe {
                core::ptr::read_unaligned((PROOF_BUF + 2352) as *const u64)
            };
            REVOKE_BASE.store(base, Ordering::SeqCst);
            REVOKE_TASK.store(task_b as u64, Ordering::SeqCst);
            let revoked_ok = match crate::experimental::caps::region_id_by_base(base) {
                Some(region) => crate::experimental::caps::revoke_region(region).is_ok(),
                None => false,
            };
            REVOKE_OK.store(if revoked_ok { 1 } else { 0 }, Ordering::SeqCst);
            let woke = crate::experimental::ipc::send(
                1,
                crate::experimental::ipc::IpcMessage {
                    sender: crate::experimental::scheduler::session_identity(),
                    seq: REVOKE_SEQ,
                    ..Default::default()
                },
            )
            .is_ok();
            REVOKE_WOKE.store(if woke { 1 } else { 0 }, Ordering::SeqCst);
        }
        if pumps >= 1_000_000 {
            serial_println!(
                "  service-0x84 proof: the two ring-3 processes did not both terminate (task {} {:?} , task {} {:?}) -- the call/answer handshake stalled",
                task_a,
                crate::experimental::scheduler::state_of(task_a),
                task_b,
                crate::experimental::scheduler::state_of(task_b)
            );
            // Halt here rather than spin: the verdict line was printed
            // without a verdict, and the harness reads the console.
            // A stall always lands here with the two processes' pages
            // still mapped: dump what each of them got to, because the
            // difference between "the peer never received" and "the peer
            // answered but nobody woke" is exactly these numbers.
            let mut peer_words = [0u64; 14];
            unsafe {
                select_peer_space();
                for (i, w) in peer_words.iter_mut().enumerate() {
                    *w = core::ptr::read_volatile((PEER_BUF + 8 * i as u64) as *const u64);
                }
                select_proof_space();
            }
            serial_println!(
                "  service-0x84 proof: peer page (ack {:#x} , base {:#x} , len {:#x} , read {:#x} , recv {:#x} , handle {:#x} , req {:#x} , retries {:#x} , reply {:#x} , wrote {:#x})",
                peer_words[0], peer_words[4], peer_words[5], peer_words[6],
                peer_words[7], peer_words[8], peer_words[9], peer_words[10],
                peer_words[11], peer_words[12]
            );
            let mut a_words = [0u64; 19];
            for (i, w) in a_words.iter_mut().enumerate() {
                *w = unsafe { core::ptr::read_volatile((PROOF_BUF + 848 + 8 * i as u64) as *const u64) };
            }
            let mut head = [0u64; 4];
            for (i, w) in head.iter_mut().enumerate() {
                *w = unsafe { core::ptr::read_volatile((PROOF_BUF + 8 * i as u64) as *const u64) };
            }
            serial_println!(
                "  service-0x84 proof: first process head (gfx {:#x} , input {:#x} , enosys {:#x} , geometry {:#x})",
                head[0], head[1], head[2], head[3]
            );
            serial_println!(
                "  service-0x84 proof: first process page (close {:#x} , base {:#x} , len {:#x} , rights {:#x} , call {:#x}) , waits {} , region holders {:?}",
                a_words[0], a_words[4], a_words[5], a_words[6], a_words[11],
                crate::experimental::caps::wait_count(),
                crate::experimental::caps::region_holders(PROOF_REGION.load(Ordering::SeqCst))
            );
            crate::hlt_loop();
        }
        pumps += 1;
        crate::experimental::scheduler::yield_task();
    }

    // The verdict is read HERE, on the coordinator, with both processes
    // gone: the region's teardown is only observable after its last holder
    // exited, which is exactly the lifetime clause this slice adds.
    crate::service84::dispatch(
        crate::service84::CALL_PROOF_DONE,
        PROOF_BUF,
        PEER_BUF,
        0,
        u64::MAX,
        0,
        0,
    );
    // The verdict is on the console; the harness reads it there. Halt
    // rather than return: there is no "after the proof" in this boot.
    crate::hlt_loop();
}
