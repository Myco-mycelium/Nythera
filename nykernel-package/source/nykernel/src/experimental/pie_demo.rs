//! Position-independent executable (`ET_DYN`) loading demo.
//!
//! Everything else this kernel has loaded so far is `ET_EXEC` --
//! fixed-address, no relocation needed. This module proves the other
//! real capability `experimental::elf` now has: loading a genuine
//! position-independent executable at a base address the loader
//! chooses, correctly applying the `R_X86_64_RELATIVE` relocations its
//! `PT_DYNAMIC` segment describes.
//!
//! See `crate::experimental`'s banner -- not NPS-002/NPS-003
//! conformance; this is a loader capability proof, same standard as
//! every other `experimental` module.

use x86_64::VirtAddr;

use crate::experimental::address_space::AddressSpace;
use crate::experimental::{elf, pe_demo, usermode};
use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::serial_println;

pub const TRAP_VECTOR: u8 = 0x82;

/// A real, separately-built `ET_DYN` executable
/// (`userprog/prog_pie.s`, linked with `ld -pie --no-dynamic-linker`
/// -- a self-contained "static-pie" binary, no interpreter, no
/// external symbols). Its `.data` holds an absolute pointer
/// (`data_ptr -> target_value`) that only reads correctly if the
/// loader's relocation processing actually ran -- see `prog_pie.s`'s
/// own comment for why this is a genuine test of relocation, not code
/// that would happen to work anyway.
static PIE_PROGRAM_ELF: &[u8] = include_bytes!("../../userprog/userprog_pie.elf");

/// A deliberately large, distinctive base address -- chosen by this
/// loader, not baked into the file (confirmed: `readelf -l` on the
/// source binary shows its `PT_LOAD` segments starting near virtual
/// address 0). Loading it here and having the relocated pointer
/// dereference correctly is only possible if `experimental::elf`
/// genuinely added this bias everywhere it needed to, not just at the
/// entry point.
const LOAD_BASE: u64 = 0x_6000_0000_000;

const PRIVATE_ADDR: u64 = 0x_6000_0010_000;
const USER_STACK_ADDR: u64 = 0x_6000_0020_000;

const EXPECTED_VALUE: u64 = 0xFEEDFACE12345678;

static mut FRAME_ALLOCATOR: Option<BumpFrameAllocator> = None;

/// Builds an address space, loads the PIE binary at [`LOAD_BASE`],
/// maps a private stack, and enters ring 3 -- a one-way transition,
/// same reasoning as `usermode::enter`/`isolation_demo`. The trap
/// handler (installed at [`TRAP_VECTOR`] by `idt::init`) reports the
/// result and chains into `experimental::pe_demo` with the same
/// frame allocator, continuing this kernel's single unified demo chain
/// rather than exiting directly.
///
/// # Safety
/// Must be called at most once, with Phases 1-4-prep already
/// initialized.
pub unsafe fn start(frame_allocator: BumpFrameAllocator) -> ! {
    FRAME_ALLOCATOR = Some(frame_allocator);
    let fa = FRAME_ALLOCATOR.as_mut().unwrap();

    let mut space = AddressSpace::new_sharing_kernel(fa)
        .unwrap_or_else(|_| panic!("failed to build pie_demo address space"));
    space.activate();
    let mut mapper = space.mapper();

    let loaded = elf::load(PIE_PROGRAM_ELF, &mut mapper, fa, LOAD_BASE)
        .unwrap_or_else(|e| panic!("failed to load PIE program: {:?}", e));

    let data_flags = x86_64::structures::paging::PageTableFlags::PRESENT
        | x86_64::structures::paging::PageTableFlags::WRITABLE
        | x86_64::structures::paging::PageTableFlags::USER_ACCESSIBLE
        | x86_64::structures::paging::PageTableFlags::NO_EXECUTE;
    let data_mapped = crate::memory::paging::map_range(
        &mut mapper, fa, VirtAddr::new(PRIVATE_ADDR), 1, data_flags,
    );
    assert_eq!(data_mapped, 1, "failed to map pie_demo private data page");

    let stack_flags = data_flags; // same permission profile: writable, non-executable, user-accessible
    let stack_mapped = crate::memory::paging::map_range(
        &mut mapper, fa, VirtAddr::new(USER_STACK_ADDR), 1, stack_flags,
    );
    assert_eq!(stack_mapped, 1, "failed to map pie_demo user stack");

    serial_println!(
        "  experimental pie-demo: ET_DYN loaded at base {:#x}, entry {:#x} (bias correctly applied by the loader, not baked into the file)",
        LOAD_BASE, loaded.entry_point.as_u64()
    );

    // `space` must outlive this function (the process runs for the
    // rest of the kernel's life) -- deliberately leaked, matching the
    // no-teardown honesty already established for every other
    // experimental address space.
    core::mem::forget(space);

    let user_rsp = VirtAddr::new(USER_STACK_ADDR + 4096 - 16);
    usermode::enter_with_stack(loaded.entry_point, user_rsp)
}

/// Called from `idt::init`'s installed handler on the one trap this
/// demo's program issues. Reports whether the relocated pointer
/// dereferenced to the expected value, then chains into
/// `experimental::isolation_demo`.
pub unsafe fn on_trap() -> ! {
    let observed = core::ptr::read_volatile(PRIVATE_ADDR as *const u64);
    let ok = observed == EXPECTED_VALUE;
    serial_println!(
        "  experimental pie-demo self-test: relocated pointer dereferenced to {:#x} (expected {:#x}) -- {}",
        observed, EXPECTED_VALUE, if ok { "OK, relocation correctly applied" } else { "MISMATCH" }
    );

    let fa = FRAME_ALLOCATOR.take().unwrap();
    pe_demo::start(fa)
}
