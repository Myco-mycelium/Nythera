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

    // The ring-0 stack the CPU will switch to on the first trap: if it is
    // not mapped in THIS address space, the first `int 0x84` faults while
    // the CPU is pushing the frame, which looks like a kernel fault at a
    // stack address rather than a service bug. Check it explicitly and say
    // so, instead of discovering it as an exception dump.
    let rsp0 = space.kernel_stack_top();
    let rsp0_flags = paging::page_flags_active(VirtAddr::new(rsp0 - 8));
    serial_println!(
        "  service-0x84 proof: program loaded at {:#x}, results page {:#x} and stack {:#x} mapped USER_ACCESSIBLE; TSS.RSP0 {:#x} is {:?}; entering ring 3",
        loaded.entry_point.as_u64(),
        PROOF_BUF,
        PROOF_STACK,
        rsp0,
        rsp0_flags
    );

    usermode::enter_with_stack(loaded.entry_point, VirtAddr::new(PROOF_STACK + 4096 - 16))
}
