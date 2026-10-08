//! Two-process address-space isolation demo.
//!
//! Orchestrates the actual proof described in
//! `experimental::address_space`'s module doc: build two separate
//! processes, each using the *identical* virtual address for its
//! code/stack, and show that neither can see or corrupt the other's
//! memory. See `crate::experimental`'s banner -- not NPS-002
//! conformance.
//!
//! This lives in its own module (rather than being folded into
//! `idt::syscall_handler`, which only knows how to record one trap's
//! CPL/hit state) because the sequence genuinely needs multi-stage
//! state: build A, run A, (on A's trap) build B, run B, (on B's trap)
//! switch back to A and verify A's memory is untouched, then finish.
//! `idt::syscall_handler` delegates here.

use core::sync::atomic::{AtomicU8, Ordering};

use crate::experimental::address_space::{self, AddressSpace};
use crate::experimental::usermode;
use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::serial_println;

static PROGRAM_A: &[u8] = include_bytes!("../../userprog/userprog.elf");
static PROGRAM_B: &[u8] = include_bytes!("../../userprog/userprog_b.elf");

pub const MAGIC_A: u64 = 0xC0FFEE;
pub const MAGIC_B: u64 = 0xBBBBBBBB;

#[repr(u8)]
#[derive(Clone, Copy, PartialEq, Eq)]
enum Stage {
    WaitingForA = 0,
    WaitingForB = 1,
    Done = 2,
}

static STAGE: AtomicU8 = AtomicU8::new(Stage::WaitingForA as u8);

// `static mut` for the same reason `experimental::scheduler::TASKS` and
// `experimental::ipc::MAILBOXES` are: single-core, no concurrent
// access (interrupts are the only other thing that could touch these,
// and the only interrupt handler that does is `on_syscall_trap` below,
// which by construction only runs when the CPU isn't already inside
// this module's own code).
static mut SPACE_A: Option<AddressSpace> = None;
static mut SPACE_B: Option<AddressSpace> = None;
static mut FRAME_ALLOCATOR: Option<BumpFrameAllocator> = None;

fn stage() -> Stage {
    match STAGE.load(Ordering::SeqCst) {
        0 => Stage::WaitingForA,
        1 => Stage::WaitingForB,
        _ => Stage::Done,
    }
}

fn set_stage(s: Stage) {
    STAGE.store(s as u8, Ordering::SeqCst);
}

/// Called once by `kernel_main`, as its own final action (same
/// one-way-`iretq` reasoning as `usermode::enter` -- see that
/// function's doc comment). Takes ownership of `frame_allocator`
/// (moved into this module's static storage, now possible without
/// lifetime friction since `BumpFrameAllocator` owns its
/// `MultibootInfo` by value) since later stages, running from inside
/// `on_syscall_trap`, need to keep allocating frames long after
/// `kernel_main`'s own locals are unreachable.
///
/// # Safety
/// Must be called at most once. Requires the kernel heap, paging, and
/// interrupt infrastructure (Phases 2-4-prep) already initialized.
pub unsafe fn start(frame_allocator: BumpFrameAllocator) -> ! {
    FRAME_ALLOCATOR = Some(frame_allocator);
    let fa = FRAME_ALLOCATOR.as_mut().unwrap();

    let mut space_a = AddressSpace::new_sharing_kernel(fa)
        .unwrap_or_else(|_| panic!("failed to build address space A"));
    space_a.activate();
    let mut mapper_a = space_a.mapper();
    let entry_a = usermode::prepare_elf(PROGRAM_A, &mut mapper_a, fa)
        .unwrap_or_else(|e| panic!("failed to load program A: {:?}", e));

    serial_println!(
        "  experimental isolation: address space A built (pml4 {:#x}), program A loaded, entry {:#x}",
        address_space::pml4_phys_addr(&space_a).as_u64(),
        entry_a.as_u64()
    );

    SPACE_A = Some(space_a);
    set_stage(Stage::WaitingForA);
    usermode::enter(entry_a)
}

/// Called by `idt::syscall_handler` on every `int 0x80` trap while
/// this demo is running. Returns `true` if it fully handled the trap
/// (including, in the terminal case, reporting results and calling
/// `qemu_exit`/`hlt_loop` -- i.e. this function may not return at all
/// on the final stage). Returns `false` if this demo isn't active,
/// so `idt::syscall_handler` falls back to its own simpler reporting
/// (used by the original single-process proof in `kernel_main`, which
/// still exists and still runs first).
///
/// # Safety
/// Must only be called from within `idt::syscall_handler`, i.e. while
/// already at CPL 0 having just trapped from the currently-active
/// address space.
pub unsafe fn on_syscall_trap(cpl: u64) -> bool {
    match stage() {
        Stage::WaitingForA => {
            let observed = core::ptr::read_volatile(usermode::USER_STACK_ADDR as *const u64);
            let ok = cpl == 3 && observed == MAGIC_A;
            serial_println!(
                "  experimental isolation: trap from process A (cpl={}), read {:#x} (expected {:#x}) -- {}",
                cpl, observed, MAGIC_A, if ok { "OK" } else { "MISMATCH" }
            );
            if !ok {
                report_failure_and_exit("process A's own memory was wrong immediately after it wrote it");
            }

            // Build B *while A's address space is still active* -- its
            // shared (kernel/heap) entries are copied from whichever
            // space is active at build time, so this doesn't matter
            // for correctness (A and B share the same kernel/heap
            // tables either way), but building it here keeps this
            // function's control flow linear rather than needing to
            // reactivate A first for no reason.
            let fa = FRAME_ALLOCATOR.as_mut().unwrap();
            let mut space_b = AddressSpace::new_sharing_kernel(fa)
                .unwrap_or_else(|_| panic!("failed to build address space B"));
            space_b.activate();
            let mut mapper_b = space_b.mapper();
            let entry_b = usermode::prepare_elf(PROGRAM_B, &mut mapper_b, fa)
                .unwrap_or_else(|e| panic!("failed to load program B: {:?}", e));

            serial_println!(
                "  experimental isolation: address space B built (pml4 {:#x}), program B loaded, entry {:#x} -- switched active, A is now inactive but preserved",
                address_space::pml4_phys_addr(&space_b).as_u64(),
                entry_b.as_u64()
            );

            SPACE_B = Some(space_b);
            set_stage(Stage::WaitingForB);
            usermode::enter(entry_b) // one-way; does not return
        }
        Stage::WaitingForB => {
            let observed_b = core::ptr::read_volatile(usermode::USER_STACK_ADDR as *const u64);
            let b_ok = cpl == 3 && observed_b == MAGIC_B;
            serial_println!(
                "  experimental isolation: trap from process B (cpl={}), read {:#x} (expected {:#x}) -- {}",
                cpl, observed_b, MAGIC_B, if b_ok { "OK" } else { "MISMATCH" }
            );

            // The actual isolation proof: switch back to A -- which
            // hasn't been touched since it was built -- and check
            // that its copy of "the same" virtual address still holds
            // A's value, not B's. If A and B had accidentally ended up
            // sharing the same physical frame (the bug this whole demo
            // exists to catch), this would now read MAGIC_B instead.
            SPACE_A.as_ref().unwrap().activate();
            let observed_a_after = core::ptr::read_volatile(usermode::USER_STACK_ADDR as *const u64);
            let isolation_ok = observed_a_after == MAGIC_A;
            serial_println!(
                "  experimental isolation: switched back to process A's address space, re-read {:#x} (expected {:#x}, unchanged since A ran) -- {}",
                observed_a_after, MAGIC_A, if isolation_ok { "OK, ISOLATED" } else { "CORRUPTED -- ISOLATION FAILED" }
            );

            set_stage(Stage::Done);
            if b_ok && isolation_ok {
                serial_println!("  experimental isolation self-test: two independent address spaces verified mutually isolated -- OK");
                // Final link in the demo chain: the guard-page overflow
                // proof (`memory::guard_demo`) is terminal by design -- a
                // real write into an unmapped guard page must fault, so
                // nothing can follow it on a correct implementation. It
                // prints the closing line and exits (success on the
                // expected fault, failure otherwise). Active space here
                // is A, so map the probe stack into A's tables.
                let fa = FRAME_ALLOCATOR.as_mut().unwrap();
                let mut mapper = SPACE_A.as_mut().unwrap().mapper();
                crate::memory::guard_demo::run_and_prove(&mut mapper, fa)
            } else {
                report_failure_and_exit("process B or the post-switch isolation check failed");
            }
        }
        Stage::Done => false,
    }
}

fn report_failure_and_exit(reason: &str) -> ! {
    serial_println!("  experimental isolation self-test: FAILED -- {}", reason);
    #[cfg(feature = "qemu_test")]
    crate::qemu_exit::exit(crate::qemu_exit::QemuExitCode::Failed);
    #[cfg(not(feature = "qemu_test"))]
    crate::hlt_loop();
}
