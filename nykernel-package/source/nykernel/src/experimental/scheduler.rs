//! Cooperative, round-robin, stack-switching task scheduler.
//!
//! See `crate::experimental` for the "this is not NPS-002 conformance"
//! disclaimer -- read it before assuming this is the kernel's real
//! process model. What this module *does* honestly demonstrate: a
//! real context switch on real (emulated) x86_64 hardware -- saving
//! one execution context's non-volatile registers and stack pointer,
//! and resuming a different one -- which is the mechanical primitive
//! any eventual NPS-002-conformant scheduler will also need
//! underneath its actual policy (capability scoping, preemption
//! fairness, etc., none of which exist here).
//!
//! Deliberately cooperative (tasks call [`yield_task`] themselves),
//! not preemptive (not tied to the timer interrupt) -- preemption adds
//! a second class of correctness bug (switching from inside an
//! interrupt handler, on the handler's stack, with a task's own stack
//! left in a not-yet-consistent state) that a first proof-of-concept
//! doesn't need to take on at the same time as "does the switch itself
//! work at all."
//!
//! Extended to also switch `CR3` as part of every task switch (see
//! [`spawn_with_cr3`]) -- a task can now have its own private address
//! space (built via `experimental::address_space`), integrated into
//! ordinary cooperative scheduling rather than the earlier,
//! hand-orchestrated `experimental::isolation_demo` sequence. See
//! `kernel_main`'s isolated-task demo for the proof this actually
//! works under *repeated* switching, not just once.

use alloc::vec::Vec;
use core::arch::naked_asm;
use x86_64::registers::control::Cr3;
use x86_64::structures::paging::OffsetPageTable;

use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::memory::kernel_stack::{self, GuardedStack};
use crate::serial_println;

/// Usable pages per task stack (4 pages = 16 KiB, unchanged from the
/// previous heap allocation), with a guard page directly below them.
const TASK_STACK_PAGES: u64 = 4;

struct TaskControlBlock {
    #[allow(dead_code)] // kept for future diagnostics (task listing, etc.)
    id: usize,
    /// The task's guarded kernel stack, from `memory::kernel_stack`.
    /// `None` only for task 0 (whatever called `init()`), which keeps
    /// running on its own existing kernel stack -- `init()` allocates
    /// nothing. The shared region never reuses a slot, so a spawned
    /// task's stack stays valid for the TCB's whole lifetime.
    stack: Option<GuardedStack>,
    /// The task's saved stack pointer while it isn't running. Written
    /// by `switch_to` every time this task is switched *away* from;
    /// read by `switch_to` every time it's switched *to*.
    saved_rsp: u64,
    /// The physical address of this task's PML4 -- `switch_to` writes
    /// this to `CR3` on every switch *to* this task, unconditionally
    /// (not just when it differs from the previous task's), which is a
    /// documented simplification: real schedulers skip the reload when
    /// unnecessary to avoid a needless TLB flush, but that's a
    /// performance optimization, not a correctness requirement, and
    /// this proof of concept prioritizes the latter. Tasks that don't
    /// need their own private address space (the original Phase 3
    /// demo's tasks A/B) simply record whatever CR3 was active when
    /// they were spawned -- see [`current_cr3`].
    cr3: u64,
}

static mut TASKS: Vec<TaskControlBlock> = Vec::new();
static mut CURRENT: usize = 0;

/// The physical address of the currently active PML4 -- used both by
/// callers spawning an ordinary (address-space-sharing) task and
/// internally to seed task 0's recorded CR3 in [`init`].
pub fn current_cr3() -> u64 {
    Cr3::read().0.start_address().as_u64()
}

/// MUST be called exactly once, before any [`spawn`] or [`yield_task`]
/// call, and only after the kernel heap is live (`memory::heap::init`)
/// -- the scheduler's own bookkeeping (`TASKS`) still uses
/// `alloc::vec::Vec`. Task *stacks*, by contrast, come from
/// `memory::kernel_stack`'s shared guarded region (see [`spawn`]), so a
/// stack overflow faults instead of corrupting the heap.
pub fn init() {
    unsafe {
        // Task 0 is whatever called init() -- its real stack pointer
        // gets captured the first time it actually yields away, via
        // switch_to's `mov [rdi], rsp`; there's nothing to precompute
        // here, which is why `stack` is None and `saved_rsp` starts at
        // a placeholder 0 that's never read before it's written. Its
        // CR3 *is* known up front, though: whatever's active right now,
        // at boot.
        TASKS.push(TaskControlBlock {
            id: 0,
            stack: None,
            saved_rsp: 0,
            cr3: current_cr3(),
        });
    }
}

/// Registers a new task and builds its initial stack frame so that the
/// first time it's switched *to*, execution starts at `entry` as if it
/// had been `call`ed -- even though it's reached via `ret`, not `call`
/// (see the layout comment inline below; this is the standard
/// stack-switch trick, not something specific to this kernel). The
/// task shares whichever address space is currently active at spawn
/// time -- for a task with its own private address space, use
/// [`spawn_with_cr3`] instead.
///
/// `mapper`/`frame_allocator` are used to allocate the task's guarded
/// kernel stack from `memory::kernel_stack`.
pub fn spawn(
    entry: extern "C" fn() -> !,
    mapper: &mut OffsetPageTable<'static>,
    frame_allocator: &mut BumpFrameAllocator,
) -> usize {
    spawn_with_cr3(entry, current_cr3(), mapper, frame_allocator)
}

/// As [`spawn`], but the task records `cr3` (a PML4 physical address,
/// e.g. from `experimental::address_space::pml4_phys_addr`) instead of
/// whatever's currently active -- `switch_to` will load it every time
/// this task runs, giving it a genuinely private address space
/// integrated into ordinary cooperative task switching rather than the
/// separate, hand-orchestrated sequence `experimental::isolation_demo`
/// uses.
///
/// `mapper`/`frame_allocator` allocate the task's guarded kernel stack
/// from `memory::kernel_stack` (see [`spawn`]).
pub fn spawn_with_cr3(
    entry: extern "C" fn() -> !,
    cr3: u64,
    mapper: &mut OffsetPageTable<'static>,
    frame_allocator: &mut BumpFrameAllocator,
) -> usize {
    // Allocate the task's guarded kernel stack from the shared region.
    // Any space's mapper works here -- the region lives under the shared
    // PML4 index, so the mapping is visible under every address space.
    let stack = kernel_stack::allocate(mapper, frame_allocator, TASK_STACK_PAGES)
        .expect("failed to allocate a guarded task stack for a scheduler task");
    // `stack.top` is already page-aligned; mask defensively for hygiene
    // (the frame layout below assumes a 16-byte-aligned rsp).
    let aligned_top = stack.top.as_u64() & !0xF;

    // From LOW address (== the rsp switch_to will restore) to HIGH:
    // [r15, r14, r13, r12, rbp, rbx, entry]. switch_to's restore path
    // pops r15..rbx in that order (6 pops), then `ret`s -- which pops
    // whatever is at that 7th slot. Putting `entry` there means the
    // very first "return" for this task lands at its own entry point.
    let frame_base = (aligned_top - 7 * 8) as *mut u64;
    unsafe {
        frame_base.add(0).write(0); // r15 (unused initial value)
        frame_base.add(1).write(0); // r14
        frame_base.add(2).write(0); // r13
        frame_base.add(3).write(0); // r12
        frame_base.add(4).write(0); // rbp
        frame_base.add(5).write(0); // rbx
        frame_base.add(6).write(entry as u64); // return address for `ret`
    }

    unsafe {
        let id = TASKS.len();
        TASKS.push(TaskControlBlock {
            id,
            stack: Some(stack),
            saved_rsp: frame_base as u64,
            cr3,
        });
        id
    }
}

/// Structural self-test: for every spawned task (all but task 0), check
/// via the page tables that its kernel stack has every page mapped and a
/// genuinely unmapped guard page directly below it. Prints one line per
/// task plus a summary, and returns whether every check held. Callable
/// with any space's mapper -- the guarded-stack region is shared.
pub fn verify_guarded_stacks(mapper: &OffsetPageTable<'static>) -> bool {
    let mut checked = 0usize;
    let mut all_ok = true;
    unsafe {
        for tcb in TASKS.iter() {
            let Some(stack) = tcb.stack else { continue };
            let ok = kernel_stack::verify(mapper, &stack);
            all_ok &= ok;
            checked += 1;
            serial_println!(
                "  experimental scheduler: task {} kernel stack {:#x}..{:#x} ({} pages) has guard page {:#x} unmapped -- {}",
                tcb.id,
                stack.base.as_u64(),
                stack.top.as_u64(),
                stack.pages,
                stack.guard.as_u64(),
                if ok { "OK" } else { "FAILED" }
            );
        }
    }
    serial_println!(
        "  experimental scheduler self-test: all {} task kernel stacks carry a genuinely unmapped guard page directly below them -- {}",
        checked,
        if all_ok { "OK" } else { "FAILED" }
    );
    all_ok
}

pub fn task_count() -> usize {
    unsafe { TASKS.len() }
}

/// Switches from whichever task is currently running to the next one
/// in round-robin order. Returns to the caller once round-robin comes
/// back around to it -- exactly like a normal function call from the
/// caller's point of view, even though a different task's code ran on
/// a different stack (and, if it has its own private address space, a
/// different set of page tables entirely) in between.
pub fn yield_task() {
    x86_64::instructions::interrupts::without_interrupts(|| unsafe {
        let task_count = TASKS.len();
        if task_count < 2 {
            return; // nothing to switch to
        }
        let current = CURRENT;
        let next = (current + 1) % task_count;
        CURRENT = next;

        let current_sp_slot: *mut u64 = &mut TASKS[current].saved_rsp;
        let next_sp = TASKS[next].saved_rsp;
        let next_cr3 = TASKS[next].cr3;
        switch_to(current_sp_slot, next_sp, next_cr3);
    });
}

/// The actual context switch. `#[naked]` because this function's ABI
/// is deliberately *not* normal Rust/C calling convention on the way
/// out -- it manipulates `rsp` itself and never executes a normal
/// `ret` back to its own caller in the usual sense (it "returns" into
/// whichever task's saved context it just restored, which might be a
/// completely different call site than this one).
///
/// # Safety
/// `current_sp_out` must point at a valid, writable `u64` (a live
/// task's `saved_rsp` field); `next_sp` must be either a previously
/// saved `saved_rsp` from a task that has yielded before, or a stack
/// frame built by `spawn`'s layout exactly as documented there. Any
/// other value will restore garbage register state and jump to a
/// garbage address. `next_cr3` must be a valid PML4 physical address
/// whose shared entries (kernel identity map + heap) match this
/// kernel's own, or the very next instruction fetch after the switch
/// will fault.
#[unsafe(naked)]
unsafe extern "C" fn switch_to(current_sp_out: *mut u64, next_sp: u64, next_cr3: u64) {
    naked_asm!(
        // Save this task's non-volatile (callee-saved, SysV x86_64) registers.
        "push rbx",
        "push rbp",
        "push r12",
        "push r13",
        "push r14",
        "push r15",
        // Stash the now-fully-saved rsp into *current_sp_out (rdi).
        "mov [rdi], rsp",
        // Switch address spaces -- rdx holds next_cr3, the 3rd SysV
        // integer argument. Done before switching rsp/registers so
        // that if something were ever wrong here, it faults
        // immediately and visibly rather than after this task is
        // already partway "resumed."
        "mov cr3, rdx",
        // Switch stacks: rsp now points into the *next* task's frame.
        "mov rsp, rsi",
        // Restore the next task's non-volatile registers from its stack.
        "pop r15",
        "pop r14",
        "pop r13",
        "pop r12",
        "pop rbp",
        "pop rbx",
        // Whatever is on top of the stack now is either a real return
        // address (a task resuming after its own past yield_task()
        // call) or a fresh task's entry point (see spawn()) -- either
        // way, `ret` is exactly the right way to transfer to it.
        "ret",
    );
}
