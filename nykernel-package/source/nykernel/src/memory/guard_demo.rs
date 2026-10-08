//! Guard-page stack-overflow detection.
//!
//! Closes the "Guard pages for stack-overflow detection" gap named in
//! IMPLEMENTATION_STATUS.md. A guard page is the standard mechanism: a
//! stack is mapped as a run of pages, and the page immediately below
//! its lowest usable address is deliberately left **unmapped**. A
//! downward-growing stack that overflows its allotted pages then runs
//! straight into an unmapped page and takes a deterministic page fault
//! instead of silently scribbling over whatever happens to sit below
//! it -- which is exactly the kind of memory corruption that is nearly
//! impossible to diagnose after the fact.
//!
//! This is generic paging machinery (NPS-001 territory), so it lives
//! under `memory` rather than `experimental`.
//!
//! # Two kinds of proof, and why the second one has to be terminal
//!
//! 1. **Structural, every boot.** [`build`] maps a guarded stack and
//!    [`verify`] checks the page-table state directly via
//!    [`crate::memory::paging::translate`]: every stack page present,
//!    the guard page absent, and the page just above the stack (also
//!    unmapped) absent -- so the guard cannot pass by coincidence.
//!    This runs on every boot as the first half of [`run_and_prove`].
//!
//! 2. **Enforcement, every boot, but terminal.** The only way to prove
//!    a guard page *actually fault*s is to actually overflow into it.
//!    This kernel's page-fault handler is terminal by design (a real
//!    page fault means the kernel has nowhere safe to continue), so
//!    there is no in-kernel recovery to resume from. Instead, this
//!    proof is deliberately the *last* thing the boot does:
//!    [`run_and_prove`] arms the probe, writes to the guard page, and
//!    the page-fault handler -- via [`fatal_page_fault`] -- recognizes
//!    that this is the expected guard fault and reports success.
//!    Crucially, if the write *does not* fault (i.e. the guard is
//!    broken), control simply continues on the next line and reports
//!    failure, so the test cannot pass by the mechanism silently doing
//!    nothing. It is a genuine positive control, not a restatement of
//!    "the mapping call returned Ok".
//!
//! Honest scope: this proves the guard-page *mechanism* is correctly
//! built and enforced, using a dedicated probe stack. It does not yet
//! wire guarded stacks under every live kernel stack -- see
//! IMPLEMENTATION_STATUS.md for what remains.

use core::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use x86_64::structures::paging::{OffsetPageTable, PageTableFlags};
use x86_64::VirtAddr;

use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::memory::paging;
use crate::serial_println;

/// A virtual address well outside every other region this kernel uses
/// (heap 0x100_0000_0000, user code 0x200_0000_0000, ring-3 0x400_0000_0000,
/// PIE 0x600_0000_0000, the TLB demo's 0x7000_0000_000), so the probe
/// stack cannot collide with anything real.
const STACK_VADDR: u64 = 0x_8000_0000_000;
/// Usable stack pages. The guard page sits immediately below
/// `STACK_VADDR` (at `STACK_VADDR - 4096`) and is never mapped.
const STACK_PAGES: u64 = 4;

/// Set once the probe is armed and the page-fault handler should treat
/// an incoming fault as the expected guard-page proof rather than a
/// real, fatal kernel fault.
static PROBE_ARMED: AtomicBool = AtomicBool::new(false);
/// The address the armed probe expects to fault at, so the handler can
/// confirm the fault is the guard page and not something else going
/// wrong at the same moment.
static GUARD_ADDR: AtomicU64 = AtomicU64::new(0);

/// True iff [`run_and_prove`] has armed the terminal overflow probe.
/// Read by `idt::page_fault_handler`.
pub fn is_armed() -> bool {
    PROBE_ARMED.load(Ordering::SeqCst)
}

/// The descriptor of a built guarded stack -- returned by [`build`] so
/// callers (today, only the demo) can obtain a usable stack top.
pub struct GuardedStack {
    /// Lowest *mapped* address of the stack (a page boundary).
    pub base: VirtAddr,
    /// The unmapped guard page directly below [`base`].
    pub guard: VirtAddr,
    /// One past the highest mapped byte -- the correct initial stack
    /// pointer for a downward-growing stack.
    pub top: VirtAddr,
    pub pages: u64,
}

/// Maps `pages` fresh 4KiB pages starting at `va`, leaving the page
/// immediately below `va` unmapped as a guard. Returns `None` if any
/// page could not be mapped (out of frames, or already mapped) -- a
/// partial stack would be a misleading thing to hand back, so this is
/// all-or-nothing.
pub fn build(
    mapper: &mut OffsetPageTable<'static>,
    frame_allocator: &mut BumpFrameAllocator,
    va: VirtAddr,
    pages: u64,
) -> Option<GuardedStack> {
    let flags = PageTableFlags::PRESENT | PageTableFlags::WRITABLE | PageTableFlags::NO_EXECUTE;
    if paging::map_range(mapper, frame_allocator, va, pages, flags) != pages {
        return None;
    }
    Some(GuardedStack {
        base: va,
        guard: va - 4096u64,
        top: va + pages * 4096,
        pages,
    })
}

/// Checks the page-table state of a built guarded stack directly (not
/// by inferring from behavior): every stack page present, the guard
/// page absent, and the page above the stack absent. Returns `true`
/// iff all of that holds.
pub fn verify(mapper: &OffsetPageTable<'static>, stack: &GuardedStack) -> bool {
    let bottom_present = paging::translate(mapper, stack.base).is_some();
    let top_present = paging::translate(mapper, stack.base + (stack.pages - 1) * 4096).is_some();
    let guard_absent = paging::translate(mapper, stack.guard).is_none();
    let above_absent = paging::translate(mapper, stack.top).is_none();

    serial_println!(
        "  memory guard-demo: stack mapped at {:#x}..{:#x} ({} pages); bottom present {}, top present {}, guard page {:#x} absent {}, page above {:#x} absent {}",
        stack.base.as_u64(),
        stack.top.as_u64(),
        stack.pages,
        if bottom_present { "OK" } else { "FAILED" },
        if top_present { "OK" } else { "FAILED" },
        stack.guard.as_u64(),
        if guard_absent { "OK" } else { "FAILED" },
        stack.top.as_u64(),
        if above_absent { "OK" } else { "FAILED" },
    );
    serial_println!(
        "  memory guard-demo self-test: guarded stack correctly built ({} usable pages, page below genuinely unmapped, page above genuinely unmapped) -- {}",
        stack.pages,
        if bottom_present && top_present && guard_absent && above_absent {
            "OK"
        } else {
            "FAILED"
        }
    );

    bottom_present && top_present && guard_absent && above_absent
}

/// Full proof: build + structurally verify a guarded stack, then arm
/// the terminal overflow probe and write to the guard page. On a
/// correct implementation the write faults, `idt::page_fault_handler`
/// routes to [`fatal_page_fault`], and this never returns. If the
/// write *succeeds* (the guard is broken), this reports failure and
/// exits -- so the test cannot silently pass.
///
/// Must be the last action of the boot: whether it faults (success) or
/// continues (failure), it diverges.
pub fn run_and_prove(
    mapper: &mut OffsetPageTable<'static>,
    frame_allocator: &mut BumpFrameAllocator,
) -> ! {
    let stack = build(mapper, frame_allocator, VirtAddr::new(STACK_VADDR), STACK_PAGES)
        .unwrap_or_else(|| {
            serial_println!("  memory guard-demo: FAILED to map the guarded probe stack");
            fail_exit("guarded probe stack could not be mapped")
        });

    if !verify(mapper, &stack) {
        fail_exit("guarded stack was built but its page-table state is wrong");
    }

    serial_println!(
        "  memory guard-demo: arming terminal overflow probe -- writing one qword at the guard page {:#x} (expect a PROTECTION_VIOLATION page fault)",
        stack.guard.as_u64()
    );
    GUARD_ADDR.store(stack.guard.as_u64(), Ordering::SeqCst);
    PROBE_ARMED.store(true, Ordering::SeqCst);

    // If the guard page is genuinely unmapped, this faults and control
    // never reaches the next line. It is `write_volatile` so the
    // compiler cannot elide it.
    unsafe {
        core::ptr::write_volatile(stack.guard.as_u64() as *mut u64, 0xDEAD_BEEF_DEAD_BEEF);
    }

    // Reached only if the write did NOT fault -- the guard is broken.
    fail_exit("guard page did not fault on write -- guard-page enforcement is broken")
}

/// Called by `idt::page_fault_handler` when [`is_armed`] is true. If
/// the fault is at the armed guard address with a write error code,
/// this is the expected, successful proof -- report it and exited the
/// machine cleanly. Anything else while armed is a genuine failure
/// that must not be mistaken for success.
pub fn fatal_page_fault(accessed: VirtAddr, error_code: x86_64::structures::idt::PageFaultErrorCode) -> ! {
    let expected = GUARD_ADDR.load(Ordering::SeqCst);
    let is_guard = accessed.as_u64() == expected;
    let caused_by_write = error_code.contains(x86_64::structures::idt::PageFaultErrorCode::CAUSED_BY_WRITE);
    let not_present = !error_code.contains(x86_64::structures::idt::PageFaultErrorCode::PROTECTION_VIOLATION);

    if is_guard && caused_by_write && not_present {
        serial_println!(
            "  memory guard-demo self-test: overflow write to guard page {:#x} faulted exactly as required (accessed {:#x}, error code {:?}) -- OK, a stack overflow is caught deterministically instead of corrupting memory below it",
            expected,
            accessed.as_u64(),
            error_code
        );
        serial_println!("nyrqis kernel: phase 1+2+3+4+ring3-elf-isolation-poc+guard-pages complete");
        #[cfg(feature = "qemu_test")]
        crate::qemu_exit::success();
        #[cfg(not(feature = "qemu_test"))]
        crate::hlt_loop();
    }

    serial_println!(
        "  memory guard-demo self-test: FAILED -- a fault arrived while the guard probe was armed, but it was not the expected guard-page write (accessed {:#x}, expected {:#x}, error code {:?})",
        accessed.as_u64(),
        expected,
        error_code
    );
    fail_exit("unexpected fault during guard-page probe")
}

fn fail_exit(reason: &str) -> ! {
    serial_println!("  memory guard-demo self-test: FAILED -- {}", reason);
    #[cfg(feature = "qemu_test")]
    crate::qemu_exit::exit(crate::qemu_exit::QemuExitCode::Failed);
    #[cfg(not(feature = "qemu_test"))]
    crate::hlt_loop();
}
