//! PE32+ loading demo -- the first real Phase 5 (Windows
//! compatibility) capability proof in this kernel, not another
//! internals prerequisite. Every other `experimental` module built
//! the mechanics (ring 3, per-process address spaces, an ELF loader
//! establishing the loader pattern); this loads Windows' own
//! executable format for the first time.
//!
//! See `crate::experimental`'s banner -- not NPS-002/NPS-003
//! conformance, and not NPS-007 conformance either (this kernel has
//! not verified NPS-007's exact current text against this work -- see
//! `engineering/RFC-0001-nykernel-bringup-plan.md`'s still-open action
//! item). This is a loader capability proof, same standard as every
//! other `experimental` module: it demonstrates the mechanism works,
//! not that Windows application compatibility is complete.

use x86_64::VirtAddr;

use crate::experimental::address_space::AddressSpace;
use crate::experimental::{pe, pe_reloc_demo, usermode};
use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::serial_println;

pub const TRAP_VECTOR: u8 = 0x83;

/// A real, separately-built PE32+ executable (`userprog/prog_pe.s`,
/// linked with `ld -m i386pep --subsystem native -e _start` -- no
/// MSVC, no MinGW runtime, just GNU `as`/`ld` producing a genuine
/// Windows-format binary). Confirmed with `file`/`objdump -p` before
/// any kernel code touched it -- see `prog_pe.s`'s own comment.
static PE_PROGRAM: &[u8] = include_bytes!("../../userprog/userprog_pe.exe");

const PRIVATE_ADDR: u64 = 0x_8000_0010_000;
const USER_STACK_ADDR: u64 = 0x_8000_0020_000;
const EXPECTED_VALUE: u64 = 0xFEEDC0DE_FEEDC0DE;

static mut FRAME_ALLOCATOR: Option<BumpFrameAllocator> = None;

/// Builds an address space, loads the PE at its own `ImageBase` (no
/// relocation -- see `experimental::pe`'s module doc for why that's
/// the correct first target), maps a private stack, and enters ring 3
/// -- a one-way transition, same reasoning as every other
/// `experimental` demo's `enter`. The trap handler chains into
/// `experimental::isolation_demo`, continuing this kernel's single
/// unified demo chain.
///
/// # Safety
/// Must be called at most once, with Phases 1-4-prep already
/// initialized.
pub unsafe fn start(frame_allocator: BumpFrameAllocator) -> ! {
    FRAME_ALLOCATOR = Some(frame_allocator);
    let fa = FRAME_ALLOCATOR.as_mut().unwrap();

    let mut space = AddressSpace::new_sharing_kernel(fa)
        .unwrap_or_else(|_| panic!("failed to build pe_demo address space"));
    space.activate();
    let mut mapper = space.mapper();

    // load_bias 0 = load at the file's own ImageBase (no relocation),
    // the fixed-address target -- base-relocation support is proven
    // separately by `pe_reloc_demo`.
    let loaded = pe::load(PE_PROGRAM, &mut mapper, fa, 0)
        .unwrap_or_else(|e| panic!("failed to load PE program: {:?}", e));

    let data_flags = x86_64::structures::paging::PageTableFlags::PRESENT
        | x86_64::structures::paging::PageTableFlags::WRITABLE
        | x86_64::structures::paging::PageTableFlags::USER_ACCESSIBLE
        | x86_64::structures::paging::PageTableFlags::NO_EXECUTE;
    let data_mapped = crate::memory::paging::map_range(
        &mut mapper, fa, VirtAddr::new(PRIVATE_ADDR), 1, data_flags,
    );
    assert_eq!(data_mapped, 1, "failed to map pe_demo private data page");
    let stack_mapped = crate::memory::paging::map_range(
        &mut mapper, fa, VirtAddr::new(USER_STACK_ADDR), 1, data_flags,
    );
    assert_eq!(stack_mapped, 1, "failed to map pe_demo user stack");

    serial_println!(
        "  experimental pe-demo: PE32+ loaded at its own ImageBase (no relocation), entry {:#x}",
        loaded.entry_point.as_u64()
    );

    core::mem::forget(space); // no teardown for this demo -- see teardown_demo for that proof separately

    let user_rsp = VirtAddr::new(USER_STACK_ADDR + 4096 - 16);
    usermode::enter_with_stack(loaded.entry_point, user_rsp)
}

/// Called from `idt::init`'s installed handler on the one trap this
/// demo's program issues. Reports whether the PE's own code actually
/// ran (via a magic value only it could have written), then chains
/// into `experimental::pe_reloc_demo` (the base-relocation proof).
pub unsafe fn on_trap() -> ! {
    let observed = core::ptr::read_volatile(PRIVATE_ADDR as *const u64);
    let ok = observed == EXPECTED_VALUE;
    serial_println!(
        "  experimental pe-demo self-test: PE entry point wrote {:#x} (expected {:#x}) -- {}",
        observed, EXPECTED_VALUE,
        if ok { "OK, a genuine Windows-format (PE32+) executable's own code ran in ring 3" } else { "MISMATCH" }
    );

    let fa = FRAME_ALLOCATOR.take().unwrap();
    pe_reloc_demo::start(fa)
}
