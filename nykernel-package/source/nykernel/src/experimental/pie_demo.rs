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

/// The loader's actual GNU_RELRO range endpoints, stashed by `start`
/// from `LoadedElf::relro` and consumed by the structural check in
/// `on_trap` -- so the checked range is what `elf::load` decided, never
/// a copied constant (a copied constant could not fail when the
/// loader's revoke pass is removed). Two atomics because each endpoint
/// is a full 64-bit address (this demo's base alone needs 44 bits).
static RELRO_START: core::sync::atomic::AtomicU64 = core::sync::atomic::AtomicU64::new(0);
static RELRO_END: core::sync::atomic::AtomicU64 = core::sync::atomic::AtomicU64::new(0);

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

    // GNU_RELRO disposition, reported as a fact from the loader's own
    // return value, and STASHED for the on-trap structural check below:
    // the checked range must be the loader's own relro range (0x2ef0..
    // 0x3000 under this pie's layout), not something this module guesses
    // -- a hand-copied constant could not fail when the loader's revoke
    // pass is removed, and a check that cannot fail is decorative.
    // `userprog/userprog_pie.elf` (built by build.sh with default
    // `ld -pie` behavior) carves its `.dynamic` segment out as a
    // GNU_RELRO region (verified via `readelf -l`: a R-only
    // PT_GNU_RELRO segment covering the same bytes as PT_DYNAMIC), and
    // its single R_X86_64_RELATIVE relocation targets `data_ptr` in
    // `.data` -- NOT inside the relro region -- so the honest outcome
    // here is MadeReadOnly with nothing needing the write-grant path.
    match loaded.relro {
        Some(elf::RelroOutcome::MadeReadOnly { start, end }) => {
            RELRO_START.store(start, core::sync::atomic::Ordering::SeqCst);
            RELRO_END.store(end, core::sync::atomic::Ordering::SeqCst);
            serial_println!(
                "  experimental pie-demo: GNU_RELRO region {:#x}..{:#x} is back to read-only after relocation (loader grant/revoke cycle completed) -- OK",
                start, end
            );
        }
        Some(elf::RelroOutcome::Denied { start, end }) => {
            panic!("GNU_RELRO region {:#x}..{:#x} was denied (unsupported relocation type); refusing to continue on a known-unprotected process", start, end);
        }
        None => panic!("GNU_RELRO outcome absent; a default `ld -pie` build always has one"),
    }

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

    // Structural GNU_RELRO enforcement check, independent of the
    // loader's own claim: walk the page tables of THIS demo's (still
    // active) address space and read the leaf PTE of every page in the
    // RELRO region. After the loader's revoke pass, none may contain
    // WRITABLE -- a direct observation of the permission bits the CPU
    // will actually enforce, not an inference from later fault
    // behavior. (`page_flags_of` walks CR3's tables, which are this
    // space's own, since `space.activate()` set it and no other address
    // space has been activated since -- but observing through CR3 is
    // deliberate either way.)
    {
        use crate::memory::paging::page_flags_of;
        // The checked range comes from the loader's own report (end
        // exclusive), page-walked at 4096 -- see the RELRO_START/END docs
        // above for why it must not be a hand-copied constant.
        let relro_start = RELRO_START.load(core::sync::atomic::Ordering::SeqCst);
        let relro_end = RELRO_END.load(core::sync::atomic::Ordering::SeqCst);
        assert!(relro_end > relro_start, "loader reported an empty RELRO range");
        let mapper = crate::experimental::address_space::active_mapper();
        let mut all_ok = true;
        let mut checked = 0usize;
        for base in (relro_start..relro_end).step_by(4096) {
            let addr = x86_64::VirtAddr::new(base);
            match page_flags_of(&mapper, addr) {
                Some(Ok(flags)) => {
                    let ro = !flags.contains(x86_64::structures::paging::PageTableFlags::WRITABLE);
                    all_ok &= ro;
                    checked += 1;
                    serial_println!(
                        "  experimental pie-demo relro check: page {:#x} PTE flags writable={} -- {}",
                        base,
                        flags.contains(x86_64::structures::paging::PageTableFlags::WRITABLE),
                        if ro { "OK" } else { "FAILED" }
                    );
                }
                Some(Err(_)) => {
                    all_ok = false;
                    serial_println!("  experimental pie-demo relro check: page {:#x} PTE read failed (huge page at an unexpected level) -- FAILED", base);
                }
                None => {
                    all_ok = false;
                    serial_println!("  experimental pie-demo relro check: page {:#x} is not mapped -- FAILED", base);
                }
            }
        }
        serial_println!(
            "  experimental pie-demo self-test: all {} GNU_RELRO page(s) verified read-only in the live page tables after relocation -- {}",
            checked,
            if all_ok { "OK" } else { "FAILED" }
        );
        assert!(all_ok, "GNU_RELRO enforcement failed: a RELRO page is still writable after the loader's revoke pass");
    }

    let fa = FRAME_ALLOCATOR.take().unwrap();
    pe_demo::start(fa)
}
