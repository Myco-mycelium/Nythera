//! PE32+ base-relocation demo -- the Phase 5 complement to
//! `experimental::pe`'s fixed-address load. Where `pe_demo` loads a PE
//! at its own `ImageBase` (no relocation), this loads a *relocatable*
//! PE at a loader-chosen address and proves the `.reloc` section's
//! `IMAGE_REL_BASED_DIR64` entry was applied: the program writes a
//! magic value THROUGH an absolute pointer stored in its own `.data`,
//! which only reaches the right (runtime) address if the loader added
//! the load delta. See `experimental::pe`'s `apply_base_relocations`.
//!
//! See `crate::experimental`'s banner -- not NPS-002/NPS-003
//! conformance, and not a claim of Windows *application*
//! compatibility: this is a loader-mechanics capability proof, same
//! standard as every other `experimental` module.

use x86_64::structures::paging::PageTableFlags;
use x86_64::VirtAddr;

use crate::experimental::address_space::AddressSpace;
use crate::experimental::{pe, pe_import_demo, usermode};
use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::memory::paging;
use crate::serial_println;

pub const TRAP_VECTOR: u8 = 0x84;

/// A real, separately-built, RELOCATABLE PE32+ executable
/// (`userprog/prog_pe_reloc.s`). Its `.data` holds `ptr`, an absolute
/// pointer to `slot`, recorded in the `.reloc` section as a `DIR64`
/// base relocation (confirmed with `objdump -p` before any kernel code
/// touched it -- the file genuinely has a nonzero
/// `IMAGE_DIRECTORY_ENTRY_BASERELOC`).
static PE_RELOC_PROGRAM: &[u8] = include_bytes!("../../userprog/userprog_pe_reloc.exe");

/// The file's own `ImageBase` (from its headers) and the loader-chosen
/// address we relocate it to. `delta = LOAD_BASE - IMAGE_BASE` is what
/// the relocation must add.
const IMAGE_BASE: u64 = 0x1_4000_0000;
const LOAD_BASE: u64 = 0x_9000_0000_000;

/// `.data` layout (from the linked file): `ptr` at RVA 0x2000, `slot`
/// at RVA 0x2008 -- the relocated pointer must end up pointing at
/// `LOAD_BASE + SLOT_RVA`.
const PTR_RVA: u64 = 0x2000;
const SLOT_RVA: u64 = 0x2008;

const USER_STACK_ADDR: u64 = 0x_9000_0010_000;
const EXPECTED_VALUE: u64 = 0xFEEDFACECAFEBEEF;

static mut FRAME_ALLOCATOR: Option<BumpFrameAllocator> = None;

/// Builds an address space, loads the relocatable PE at [`LOAD_BASE`]
/// (applying its base relocations), maps a private stack, and enters
/// ring 3. The trap handler chains into `experimental::isolation_demo`,
/// keeping the single unified demo chain.
///
/// # Safety
/// Must be called at most once, with Phases 1-4-prep already
/// initialized.
pub unsafe fn start(frame_allocator: BumpFrameAllocator) -> ! {
    FRAME_ALLOCATOR = Some(frame_allocator);
    let fa = (*&raw mut FRAME_ALLOCATOR).as_mut().unwrap();

    // This stage's kernel stack is sized DELIBERATELY (8 pages, not
    // the one-size default of 5): the on_trap continuation below
    // CHAINS into pe_import_demo::start with an ordinary call, so this
    // stack hosts not only this stage's own body but the whole
    // pe-import demo's body too -- and that fact family has grown
    // round by round (imports, forwarders, bounds, forwarder refs,
    // two-ref chains, delay-loads) until the first delay-load boots
    // overflowed the default budget loudly at the guard page (a #DF,
    // zero corruption -- exactly the failure the guard exists to
    // surface). Per-stage budgeting is the established mechanism: the
    // accumulated-chain era used 8 pages before the chain partitioned;
    // this stage's measured need today is the same 8.
    let mut space = AddressSpace::new_sharing_kernel_with_stack_pages(fa, 8)
        .unwrap_or_else(|_| panic!("failed to build pe_reloc_demo address space"));
    space.activate();
    let mut mapper = space.mapper();

    let loaded = pe::load(PE_RELOC_PROGRAM, &mut mapper, fa, LOAD_BASE)
        .unwrap_or_else(|e| panic!("failed to load relocatable PE program: {:?}", e));

    let stack_flags = PageTableFlags::PRESENT
        | PageTableFlags::WRITABLE
        | PageTableFlags::USER_ACCESSIBLE
        | PageTableFlags::NO_EXECUTE;
    let stack_mapped = paging::map_range(
        &mut mapper,
        fa,
        VirtAddr::new(USER_STACK_ADDR),
        1,
        stack_flags,
    );
    assert_eq!(stack_mapped, 1, "failed to map pe_reloc_demo user stack");

    // Read the loader-relocated `.data` pointer back BEFORE running the
    // program: it must now hold LOAD_BASE + SLOT_RVA (its original
    // file value was IMAGE_BASE + SLOT_RVA). This is the relocation
    // itself, observed directly.
    let relocated_ptr = core::ptr::read_volatile((LOAD_BASE + PTR_RVA) as *const u64);
    let expected_ptr = LOAD_BASE + SLOT_RVA;
    serial_println!(
        "  experimental pe-reloc-demo: PE32+ loaded at {:#x} (its ImageBase is {:#x}); .data pointer relocated to {:#x} (expected {:#x})",
        LOAD_BASE, IMAGE_BASE, relocated_ptr, expected_ptr
    );
    assert_eq!(
        relocated_ptr, expected_ptr,
        "base relocation was not applied to the .data pointer"
    );

    core::mem::forget(space); // no teardown for this demo -- see teardown_demo

    let user_rsp = VirtAddr::new(USER_STACK_ADDR + 4096 - 16);
    usermode::enter_with_stack(loaded.entry_point, user_rsp)
}

/// Called from `idt::init`'s installed handler on the one trap this
/// demo's program issues. Reports whether the PE's own code wrote the
/// magic value through the relocated pointer, then chains into
/// `experimental::isolation_demo`.
pub unsafe fn on_trap() -> ! {
    let observed = core::ptr::read_volatile((LOAD_BASE + SLOT_RVA) as *const u64);
    let ok = observed == EXPECTED_VALUE;
    serial_println!(
        "  experimental pe-reloc-demo self-test: relocated pointer wrote {:#x} (expected {:#x}) -- {}",
        observed,
        EXPECTED_VALUE,
        if ok {
            "OK, a relocatable PE32+ binary ran correctly at a loader-chosen address"
        } else {
            "MISMATCH"
        }
    );

    let fa = (*&raw mut FRAME_ALLOCATOR).take().unwrap();
    pe_import_demo::start(fa)
}
