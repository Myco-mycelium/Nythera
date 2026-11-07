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

use x86_64::structures::paging::{FrameDeallocator, Mapper, Page, PageTableFlags, Size4KiB};
use x86_64::VirtAddr;

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
static REGION_FIRST_WORD: AtomicU64 = AtomicU64::new(0);
static REGION_LAST_WORD: AtomicU64 = AtomicU64::new(0);
static REGION_FIRST_BASE: AtomicU64 = AtomicU64::new(0);
static REGION_LAST_BASE: AtomicU64 = AtomicU64::new(0);

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
    let word = core::ptr::read_volatile(geometry.base as *const u64);
    let n = REGION_RELEASED.fetch_add(1, Ordering::SeqCst);
    if n == 0 {
        REGION_FIRST_WORD.store(word, Ordering::SeqCst);
        REGION_FIRST_BASE.store(geometry.base, Ordering::SeqCst);
    }
    REGION_LAST_WORD.store(word, Ordering::SeqCst);
    REGION_LAST_BASE.store(geometry.base, Ordering::SeqCst);

    let mut mapper = crate::experimental::address_space::active_mapper();
    for i in 0..geometry.len / 4096 {
        let page: Page<Size4KiB> =
            Page::containing_address(VirtAddr::new(geometry.base + i * 4096));
        if let Ok((frame, flush)) = mapper.unmap(page) {
            flush.flush();
            (*fa).deallocate_frame(frame);
        }
    }
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
    pub released: u64,
    pub first_word: u64,
    pub last_word: u64,
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
    Some(RequestedRegionReport {
        first_unmapped: unmapped(SHM_REQUEST_WINDOW),
        second_unmapped: unmapped(SHM_REQUEST_WINDOW + SHM_REQUEST_STRIDE),
        released: REGION_RELEASED.load(Ordering::SeqCst),
        first_word: REGION_FIRST_WORD.load(Ordering::SeqCst),
        last_word: REGION_LAST_WORD.load(Ordering::SeqCst),
        first_base: REGION_FIRST_BASE.load(Ordering::SeqCst),
        last_base: REGION_LAST_BASE.load(Ordering::SeqCst),
        frames_in_use: unsafe { (*fa).frames_in_use() },
        frames_baseline: REGION_FRAMES_BASELINE.load(Ordering::SeqCst),
    })
}

/// The peer process's own load address (userprog/linker_svc84_peer.ld):
/// 7 << 40. Its results page and stack are the same two offsets the first
/// program uses, relative to ITS anchor -- the two processes share this
/// tree's single address space, so the anchors are what keeps them apart.
pub const PEER_CODE: u64 = 0x7u64 << 40;
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
    // The PEER process: its own ELF at its own anchor, in the SAME
    // address space (this tree has no per-process address space yet, and
    // pretending otherwise here would be the lie the whole two-process
    // slice exists to avoid: what is proven is two TASKS, two sessions and
    // two capability tables meeting in one page).
    let peer_loaded = elf::load(PEER_PROGRAM, &mut mapper, frame_allocator, 0, &elf::LoadContext::none())
        .unwrap_or_else(|e| panic!("svc84 proof: loading userprog_svc84_peer.elf failed: {:?}", e));
    let peer_buf_mapped =
        paging::map_range(&mut mapper, frame_allocator, VirtAddr::new(PEER_BUF), 1, flags);
    let peer_stack_mapped =
        paging::map_range(&mut mapper, frame_allocator, VirtAddr::new(PEER_STACK), 1, flags);
    assert_eq!(peer_buf_mapped, 1, "svc84 proof: peer results page not mapped");
    assert_eq!(peer_stack_mapped, 1, "svc84 proof: peer stack page not mapped");

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
    let task_a = crate::experimental::scheduler::spawn(ring3_a, &mut mapper, frame_allocator);
    let task_b = crate::experimental::scheduler::spawn(ring3_b, &mut mapper, frame_allocator);

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
            for (i, w) in peer_words.iter_mut().enumerate() {
                *w = unsafe { core::ptr::read_volatile((PEER_BUF + 8 * i as u64) as *const u64) };
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
