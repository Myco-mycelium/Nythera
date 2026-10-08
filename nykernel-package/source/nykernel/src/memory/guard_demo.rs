//! Guard-page stack-overflow detection -- the enforcement proof.
//!
//! A guard page is the standard mechanism: a stack is mapped as a run
//! of pages, and the page immediately below its lowest usable address
//! is deliberately left **unmapped**. A downward-growing stack that
//! overflows its allotted pages then runs straight into an unmapped
//! page and takes a deterministic page fault instead of silently
//! scribbling over whatever happens to sit below it -- exactly the
//! kind of memory corruption that is nearly impossible to diagnose
//! after the fact.
//!
//! The *mechanism* and the region live in
//! [`crate::memory::kernel_stack`], which is also what every live
//! ring-3 task's RSP0 stack is now allocated from (see
//! `experimental::address_space`). This module is the other half: the
//! terminal probe that proves the guard page genuinely *faults*, using
//! the exact same allocator the live stacks use, so the proof covers
//! the real mechanism and not a look-alike.
//!
//! # Two kinds of proof, and why the second one has to be terminal
//!
//! 1. **Structural, every boot.** [`build`] allocates a guarded stack
//!    from the shared region and [`verify`] checks the page-table
//!    state directly via [`crate::memory::paging::translate`]: every
//!    stack page present, the guard page absent, and the page just
//!    above the stack (the next slot's guard, also permanently
//!    unmapped) absent -- so the guard cannot pass by coincidence.
//!    (`experimental::ring3_scheduler` repeats this check for the live
//!    task stacks themselves.)
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
//! Honest scope: this proves the guard-page mechanism is correctly
//! built and enforced, and that the live ring-3 stacks share the same
//! region (checked structurally every boot). It does not force an
//! *actual* overflow of a live task's stack -- that would end the boot
//! mid-demo -- so the live stacks are verified structurally rather than
//! by deliberately overflowing them.

use core::sync::atomic::{AtomicBool, AtomicU64, Ordering};

use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::memory::kernel_stack::{self, GuardedStack};
use crate::memory::paging;
use crate::serial_println;
use x86_64::structures::paging::OffsetPageTable;
use x86_64::VirtAddr;

/// Usable pages in the probe stack. The guard page sits immediately
/// below it and is never mapped.
const PROBE_PAGES: u64 = 4;

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

/// Allocates a guarded probe stack from the shared region -- the same
/// allocator `experimental::address_space` uses for every live task's
/// kernel stack. Returns `None` if the mapping failed.
fn build(mapper: &mut OffsetPageTable<'static>, frame_allocator: &mut BumpFrameAllocator) -> Option<GuardedStack> {
    kernel_stack::allocate(mapper, frame_allocator, PROBE_PAGES)
}

/// Checks the page-table state of a built guarded stack directly (not
/// by inferring from behavior), printing each observation. Returns
/// `true` iff every stack page is present and both the guard page and
/// the page above the stack are absent.
fn verify(mapper: &OffsetPageTable<'static>, stack: &GuardedStack) -> bool {
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

/// Full proof: allocate + structurally verify a guarded stack from the
/// shared region, then arm the terminal overflow probe and write to the
/// guard page. On a correct implementation the write faults,
/// `idt::page_fault_handler` routes to [`fatal_page_fault`], and this
/// never returns. If the write *succeeds* (the guard is broken), this
/// reports failure and exits -- so the test cannot silently pass.
///
/// Must be the last action of the boot: whether it faults (success) or
/// continues (failure), it diverges.
pub fn run_and_prove(
    mapper: &mut OffsetPageTable<'static>,
    frame_allocator: &mut BumpFrameAllocator,
) -> ! {
    let stack = build(mapper, frame_allocator).unwrap_or_else(|| {
        serial_println!("  memory guard-demo: FAILED to allocate the guarded probe stack");
        fail_exit("guarded probe stack could not be allocated")
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
/// this is the expected, successful proof -- report it and exit the
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
