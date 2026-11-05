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

use x86_64::structures::paging::PageTableFlags;
use x86_64::VirtAddr;

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
    assert_eq!(buf_mapped, 1, "svc84 proof: results page not mapped");
    assert_eq!(stack_mapped, 1, "svc84 proof: stack page not mapped");

    // The proof drives the IPC rows through endpoint 0 (the program's
    // SEND/RECV choice): make that endpoint exist and be EMPTY. A proof
    // boot reaches this before the ordinary ipc::init/demo phases have
    // built a table, so it may not exist at all yet; and, since the proof
    // enters ring 3 and never returns, anything some earlier phase had
    // queued there is unreachable and no one waits for it -- draining
    // loses nothing observable. See ipc::prepare_endpoint0_for_proof.
    crate::experimental::ipc::prepare_endpoint0_for_proof();

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
    const PROOF_SESSION_ID: usize = 7;
    let (cap_ep, cap_handle, input_handle) =
        crate::experimental::caps::prepare_session_for_proof(PROOF_SESSION_ID);
    if cap_ep != 0 {
        panic!("svc84 proof: session equip produced endpoint {} -- the program names endpoint 0", cap_ep);
    }
    if cap_handle != 1 {
        panic!("svc84 proof: session equip produced handle {} -- the program names handle 1", cap_handle);
    }
    if input_handle != 2 {
        panic!("svc84 proof: session equip produced INPUT handle {} -- the program names handle 2", input_handle);
    }
    crate::service84::attach_gate_session(PROOF_SESSION_ID);

    // The DISTINCT TABLES the session machine buys, asserted at the
    // kernel side, not trusted from the code's shape: task 0's holder
    // does not exist at all, so resolving a capability AS TASK 0 must
    // refuse BadProcess. Under the retired shared-table equip (task 0's
    // holder minted by `prepare_task0_for_proof`) this check could not
    // answer BadProcess -- that is exactly what it pins.
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

    // The ring-0 stack the CPU will switch to on the first trap: if it is
    // not mapped in THIS address space, the first `int 0x84` faults while
    // the CPU is pushing the frame, which looks like a kernel fault at a
    // stack address rather than a service bug. Check it explicitly and say
    // so, instead of discovering it as an exception dump.
    let rsp0 = space.kernel_stack_top();
    let rsp0_flags = paging::page_flags_active(VirtAddr::new(rsp0 - 8));
    serial_println!(
        "  service-0x84 proof: program loaded at {:#x}, results page {:#x} and stack {:#x} mapped USER_ACCESSIBLE; TSS.RSP0 {:#x} is {:?}; session {} equip -- endpoint {} granted to the session's OWN table at handle {} , INPUT cap at handle {} (task 0's table absent: distinct, verified) ; {} scancodes injected through the controller (IRQ1 -> raw ring) ; entering ring 3",
        loaded.entry_point.as_u64(),
        PROOF_BUF,
        PROOF_STACK,
        rsp0,
        rsp0_flags,
        PROOF_SESSION_ID,
        cap_ep,
        cap_handle,
        input_handle,
        injected
    );

    usermode::enter_with_stack(loaded.entry_point, VirtAddr::new(PROOF_STACK + 4096 - 16))
}
