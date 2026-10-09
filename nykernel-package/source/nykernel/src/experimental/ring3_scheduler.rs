//! Real ring-3 task scheduling.
//!
//! `experimental::isolation_demo` proves ring-3 execution with
//! per-process address spaces, but drives it by hand -- each trap is
//! terminal (report-and-halt, or explicitly build-and-switch-to-the-
//! next-process). `experimental::isolated_tasks` proves address-space
//! switching integrated into the ordinary cooperative scheduler, but
//! its tasks run in ring 0. This module is the combination flagged as
//! the next real integration point at the end of both: ring-3 tasks
//! that can be suspended and later genuinely *resumed*, with their
//! complete execution state intact, via repeated trap-based yields --
//! not reported-and-abandoned.
//!
//! See `crate::experimental`'s banner -- not NPS-002 conformance.
//!
//! # Why this needs its own trap handler, not `extern "x86-interrupt"`
//!
//! Every other interrupt handler in this kernel uses the `x86_64`
//! crate's `extern "x86-interrupt"` convention, which only exposes the
//! architectural interrupt frame (RIP/CS/RFLAGS/RSP/SS) -- enough to
//! report what happened, not enough to save and later restore a
//! task's complete register state. This module installs a raw,
//! hand-written `#[naked]` handler at its own dedicated vector
//! ([`YIELD_VECTOR`], `0x81` -- distinct from `0x80`, which
//! `usermode`/`isolation_demo` use for one-shot report-and-halt traps)
//! that manually saves and restores all general-purpose registers.
//!
//! # Trap-saved context layout
//!
//! From LOW address (the value stored as a task's `saved_trap_rsp`) to
//! HIGH:
//!
//! ```text
//! r15 r14 r13 r12 r11 r10 r9 r8 rbp rdi rsi rdx rcx rbx rax | RIP CS RFLAGS RSP SS
//! \_____________ software-pushed by ring3_yield_trap_entry ____________/ \_ hardware _/
//! ```
//!
//! 20 `u64` slots (160 bytes) total. [`ring3_yield_trap_entry`]'s
//! push/pop sequence and [`build_initial_blob`] (for a task that has
//! never actually trapped yet) both produce/consume exactly this
//! layout; if either ever changes, both must change together.
//!
//! # Two reschedule points: the task's own `int 0x81`, and IRQ0
//!
//! [`start`] runs the cooperative proof first (tasks E, F, G
//! round-robin via 21 voluntary yields). [`finale`] then arms
//! [`PREEMPT_ARMED`] and enters two fresh tasks (H, I) built from a
//! program that never traps at all (`userprog/prog_spin.s`), after
//! which the PIT's timer interrupt is the ONLY thing that can switch
//! tasks: [`timer_entry`]/[`timer_dispatch`] push the exact same blob
//! this layout describes and round-robin [`TASKS`] exactly like
//! [`ring3_yield_dispatch`], just with the clock instead of the task
//! as the caller. Reaching [`preempt_finale`] at all proves a
//! non-yielding ring-3 task can be forced off the CPU (a
//! cooperative-only kernel would spin forever there and the boot test
//! would time out); its self-test additionally pins the exact
//! per-task preemption counts and that zero cooperative yields
//! happened during the phase.

use alloc::vec::Vec;
use core::arch::naked_asm;
use core::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use x86_64::structures::idt::InterruptDescriptorTable;
use x86_64::structures::paging::PageTableFlags;
use x86_64::{PrivilegeLevel, VirtAddr};

use crate::experimental::address_space::AddressSpace;
use crate::experimental::{elf, usermode};
use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::memory::kernel_stack;
use crate::memory::paging;
use crate::pic;
use crate::serial_println;

pub const YIELD_VECTOR: u8 = 0x81;

/// A real, separately-built ELF64 executable (`userprog/prog_yield.s`
/// + `userprog/linker_yield.ld`) -- the SAME compiled binary is loaded
/// into three separate address spaces (tasks "E", "F", "G"); isolation
/// is proven by each one's own private memory independently reaching
/// its own expected count, exactly as in `experimental::isolated_tasks`,
/// but now for real ring-3 code suspended and resumed via trap-based
/// context switching. (Generalized from the original two-task version
/// to close the "more than two tasks in the ring-3 round-robin" gap.)
static YIELD_PROGRAM_ELF: &[u8] = include_bytes!("../../userprog/userprog_yield.elf");

/// A real, separately-built ELF64 executable (`userprog/prog_spin.s`,
/// linked at the same address as the yield program -- the two are
/// never co-resident in one address space) -- an infinite loop with
/// NO trap instruction anywhere in it: increment a register counter,
/// mirror it to the private data page, jump back. Loaded into the two
/// fresh address spaces of the timer-preemption proof (tasks "H" and
/// "I"): these tasks can never yield, so only IRQ0 can switch away
/// from them.
static SPIN_PROGRAM_ELF: &[u8] = include_bytes!("../../userprog/userprog_spin.elf");

/// Where `prog_yield.s` writes its counter -- matches the hardcoded
/// immediate in that file.
const PRIVATE_ADDR: u64 = 0x_4000_0000_000;
const USER_STACK_ADDR: u64 = 0x_4000_0010_000;

/// Total `int 0x81` traps to process before stopping, with 3 tasks in
/// strict round-robin. 21 is divisible by 3, so each task yields
/// exactly 7 times -- the same worked-through trap-numbering reasoning
/// as the original two-task version (see `PHASES.md`/
/// `IMPLEMENTATION_STATUS.md`), just with a 3-cycle instead of a
/// 2-cycle: task E traps 1,4,7,...,19; F traps 2,5,...,20; G traps
/// 3,6,...,21, and the 21st trap triggers the finale.
const TARGET_YIELDS: u32 = 21;
const EXPECTED_PER_TASK: u64 = (TARGET_YIELDS / 3) as u64;

/// Armed only for the dedicated timer-preemption proof (set by
/// [`finale`] immediately before entering task H, cleared as the
/// first act of [`preempt_finale`]). While false, [`timer_entry`]
/// resumes the interrupted context on every path -- so every other
/// boot phase (all of ring 0, the E/F/G cooperative round-robin, and
/// all later ring-3 demos) behaves exactly as if this module's timer
/// handler did not exist.
static PREEMPT_ARMED: AtomicBool = AtomicBool::new(false);

/// Timer-driven context switches completed while armed;
/// [`preempt_finale`] takes over on the [`PREEMPT_TARGET`]-th one.
static PREEMPT_SWITCHES: AtomicU32 = AtomicU32::new(0);

/// Per-proof-task count of times that task was forcibly taken off the
/// CPU by IRQ0 (indices match [`TASKS`]: 0 = H, 1 = I). Strict
/// round-robin over two tasks makes both equal `PREEMPT_TARGET / 2`
/// at the end -- an exact, timing-independent assertion rather than a
/// bare "> 0".
static HOG_PREEMPTED: [AtomicU32; 2] = [AtomicU32::new(0), AtomicU32::new(0)];

/// [`YIELDS_TOTAL`] snapshotted when preemption was armed -- asserted
/// unchanged by [`preempt_finale`], proving the proof phase completed
/// on timer switches alone (the proof programs contain no trap; this
/// is the tripwire against the two task sets ever overlapping).
static HOG_ARM_YIELDS: AtomicU32 = AtomicU32::new(0);

/// Total timer-driven switches that complete the proof: 6 = 3 full
/// rounds over the 2 hogs. At the PIT's default ~18.2 Hz that's
/// ~330ms of added boot time (6 ticks out of the boot test's
/// 15-second budget), and each task gets ~55ms of ring-3 execution
/// per activation -- vastly more spin iterations than its counter
/// needs to be nonzero.
const PREEMPT_TARGET: u32 = 6;

struct Ring3Tcb {
    #[allow(dead_code)]
    id: usize,
    address_space: AddressSpace,
    saved_trap_rsp: u64,
}

static mut TASKS: Vec<Ring3Tcb> = Vec::new();
static mut CURRENT: usize = 0;
static YIELDS_TOTAL: AtomicU32 = AtomicU32::new(0);
static mut FRAME_ALLOCATOR: Option<BumpFrameAllocator> = None;

/// Installs the yield-trap handler at [`YIELD_VECTOR`] with
/// `DPL=Ring3` (a ring-3 `int 0x81` to a DPL-0 gate would itself
/// fault -- same reasoning as `idt::syscall_handler`'s vector 0x80
/// gate). Called from `idt::init`, alongside the CPU exception
/// handlers and the ordinary syscall vector.
pub fn install(idt: &mut InterruptDescriptorTable) {
    unsafe {
        idt[YIELD_VECTOR as usize]
            .set_handler_addr(VirtAddr::new(ring3_yield_trap_entry as u64))
            .set_privilege_level(PrivilegeLevel::Ring3);
    }
}

fn build_ring3_task(
    frame_allocator: &mut BumpFrameAllocator,
    id: usize,
    is_first: bool,
    program: &[u8],
) -> (Ring3Tcb, Option<VirtAddr>, Option<VirtAddr>) {
    let mut space = AddressSpace::new_sharing_kernel(frame_allocator)
        .unwrap_or_else(|_| panic!("failed to build ring3 task {} address space", id));
    unsafe { space.activate() };
    let mut mapper = space.mapper();

    let loaded = elf::load(program, &mut mapper, frame_allocator, 0)
        .unwrap_or_else(|e| panic!("failed to load yield program for task {}: {:?}", id, e));

    let flags = PageTableFlags::PRESENT | PageTableFlags::WRITABLE | PageTableFlags::USER_ACCESSIBLE | PageTableFlags::NO_EXECUTE; // data + stack pages, never meant to be executed
    let data_mapped = paging::map_range(&mut mapper, frame_allocator, VirtAddr::new(PRIVATE_ADDR), 1, flags);
    let stack_mapped = paging::map_range(&mut mapper, frame_allocator, VirtAddr::new(USER_STACK_ADDR), 1, flags);
    assert_eq!(data_mapped, 1, "failed to map private data page for task {}", id);
    assert_eq!(stack_mapped, 1, "failed to map user stack page for task {}", id);

    let user_rsp = VirtAddr::new(USER_STACK_ADDR + 4096 - 16);

    if is_first {
        // Task 0 is kicked off via usermode::enter_with_stack (a real
        // iretq, not a resume-from-saved-blob) -- its saved_trap_rsp
        // is meaningless until its own first real trap writes it, so
        // 0 is a safe, never-read placeholder.
        let tcb = Ring3Tcb { id, address_space: space, saved_trap_rsp: 0 };
        (tcb, Some(loaded.entry_point), Some(user_rsp))
    } else {
        let user_cs = crate::gdt::user_code_selector().0 as u64 | 3;
        let user_ss = crate::gdt::user_data_selector().0 as u64 | 3;
        let saved_trap_rsp = build_initial_blob(
            space.kernel_stack_top(),
            loaded.entry_point.as_u64(),
            user_rsp.as_u64(),
            user_cs,
            user_ss,
        );
        let tcb = Ring3Tcb { id, address_space: space, saved_trap_rsp };
        (tcb, None, None)
    }
}

/// Hand-constructs a saved-trap-context blob (see the module doc's
/// layout diagram) for a task that has never actually trapped yet, so
/// the ordinary resume path in [`ring3_yield_trap_entry`] can restore
/// it uniformly -- whether it's resuming a task that really did trap
/// before, or "resuming" one for what is actually its first run, the
/// asm doesn't need to know the difference.
fn build_initial_blob(kernel_stack_top: u64, entry: u64, user_rsp: u64, user_cs: u64, user_ss: u64) -> u64 {
    const BLOB_QWORDS: u64 = 20;
    let base = (kernel_stack_top - BLOB_QWORDS * 8) as *mut u64;
    unsafe {
        for i in 0..15u64 {
            base.add(i as usize).write(0); // r15..rax, initial value irrelevant
        }
        base.add(15).write(entry); // RIP
        base.add(16).write(user_cs); // CS
        base.add(17).write(0x200); // RFLAGS (IF=1)
        base.add(18).write(user_rsp); // RSP
        base.add(19).write(user_ss); // SS
    }
    base as u64
}

/// Builds tasks E, F, and G, then kicks off E via a real `iretq` (E has
/// never trapped, so there's no saved context to resume from -- this
/// is the same one-way transition `usermode::enter`/`isolation_demo`
/// use, just parameterized with E's own private stack instead of
/// `usermode::USER_STACK_ADDR`). All subsequent switching, including
/// F's and G's first-ever activations, happens through
/// [`ring3_yield_trap_entry`]'s ordinary resume path -- see
/// [`build_ring3_task`]'s `is_first` handling for why F/G need a
/// hand-built initial blob and E doesn't.
///
/// # Safety
/// Must be called at most once, after Phases 1-4-prep are initialized
/// (heap, paging, IDT with [`install`] already wired in via
/// `idt::init`).
pub unsafe fn start(frame_allocator: BumpFrameAllocator) -> ! {
    FRAME_ALLOCATOR = Some(frame_allocator);
    let fa = FRAME_ALLOCATOR.as_mut().unwrap();

    let (task_e, e_entry, e_rsp) = build_ring3_task(fa, 0, true, YIELD_PROGRAM_ELF);
    let (task_f, _, _) = build_ring3_task(fa, 1, false, YIELD_PROGRAM_ELF);
    let (task_g, _, _) = build_ring3_task(fa, 2, false, YIELD_PROGRAM_ELF);

    serial_println!(
        "  experimental ring3-scheduler: 3 tasks built (E entry {:#x}; F, G pre-loaded with initial trap blobs), target {} total yields ({} per task)",
        e_entry.unwrap().as_u64(), TARGET_YIELDS, EXPECTED_PER_TASK
    );

    TASKS.push(task_e);
    TASKS.push(task_f);
    TASKS.push(task_g);
    CURRENT = 0;

    // Structural proof that the guard-page mechanism is now wired under
    // the stacks these tasks actually trap onto, not only under the
    // dedicated terminal probe. Every stack here was allocated from
    // `memory::kernel_stack`'s shared region, so any task's mapper can
    // see all three (the region lives under the shared PML4 index).
    {
        let descriptors = [
            *TASKS[0].address_space.kernel_stack(),
            *TASKS[1].address_space.kernel_stack(),
            *TASKS[2].address_space.kernel_stack(),
        ];
        let mapper = TASKS[2].address_space.mapper();
        let names = [b'E', b'F', b'G'];
        let mut all_ok = true;
        for (i, stack) in descriptors.iter().enumerate() {
            let ok = kernel_stack::verify(&mapper, stack);
            all_ok &= ok;
            serial_println!(
                "  experimental ring3-scheduler: task {} kernel RSP0 stack {:#x}..{:#x} ({} pages) has guard page {:#x} unmapped -- {}",
                names[i] as char,
                stack.base.as_u64(),
                stack.top.as_u64(),
                stack.pages,
                stack.guard.as_u64(),
                if ok { "OK" } else { "FAILED" }
            );
        }
        serial_println!(
            "  experimental ring3-scheduler self-test: all {} live task kernel stacks carry a genuinely unmapped guard page directly below them -- {}",
            descriptors.len(),
            if all_ok { "OK" } else { "FAILED" }
        );
    }

    TASKS[0].address_space.activate();
    usermode::enter_with_stack(e_entry.unwrap(), e_rsp.unwrap())
}

/// Called (via `call {dispatch}` from the naked asm in
/// [`ring3_yield_trap_entry`]) on every `int 0x81`. Records the
/// outgoing task's just-saved context pointer, decides whether to
/// keep going or finish, and returns the incoming task's saved
/// context pointer for the asm to resume from -- or diverges into
/// [`finale`] instead, on the last yield.
#[no_mangle]
extern "C" fn ring3_yield_dispatch(trap_rsp: u64) -> u64 {
    unsafe {
        let current = CURRENT;
        TASKS[current].saved_trap_rsp = trap_rsp;

        let total = YIELDS_TOTAL.fetch_add(1, Ordering::SeqCst) + 1;
        if total >= TARGET_YIELDS {
            finale(); // diverges
        }

        let next = (current + 1) % TASKS.len();
        CURRENT = next;
        TASKS[next].address_space.activate();
        TASKS[next].saved_trap_rsp
    }
}

/// The timer's IDT entry point (vector 0x20), installed by
/// `crate::idt::init` in place of the old `extern "x86-interrupt"`
/// handler. `#[naked]` for the same reason
/// [`ring3_yield_trap_entry`] is: preempting a task means saving and
/// restoring ALL general-purpose registers around the switch, which
/// the x86-interrupt ABI's architectural frame (RIP/CS/RFLAGS/RSP/SS)
/// cannot express, and this function's exit is an `iretq` back into
/// whichever context [`timer_dispatch`] picked -- not a `ret` to a
/// caller in the ABI sense.
///
/// The push order is byte-for-byte identical to
/// [`ring3_yield_trap_entry`], so an interrupt taken from ring 3
/// produces the exact same 20-slot saved blob the resume path above
/// already understands; taken from ring 0 the hardware frame is only
/// 3 slots and no switch ever happens (see [`timer_dispatch`]).
///
/// # Safety
/// Called by the CPU only, via a 64-bit interrupt gate at vector
/// 0x20. After the 15 pushes, `sub rsp, rax` (rax = rsp & 8) pads the
/// stack down to the 16-byte SysV call boundary when needed -- rsp is
/// always 8-aligned, so the pad is 0 or 8 -- and the pad lives
/// strictly *below* the blob, so the saved layout this module
/// documents never changes.
#[unsafe(naked)]
pub unsafe extern "C" fn timer_entry() {
    naked_asm!(
        // Same push order as ring3_yield_trap_entry (rax first --
        // highest, adjacent to the hardware frame -- r15 last --
        // lowest): the blob layout must match for both paths' resume
        // code to be interchangeable.
        "push rax",
        "push rbx",
        "push rcx",
        "push rdx",
        "push rsi",
        "push rdi",
        "push rbp",
        "push r8",
        "push r9",
        "push r10",
        "push r11",
        "push r12",
        "push r13",
        "push r14",
        "push r15",
        // rdi = pointer to the saved blob (SysV 1st arg), exactly as
        // the yield trap passes it.
        "mov rdi, rsp",
        // Re-align to 16 bytes for the call: 0 or 8, branch-free.
        "mov rax, rsp",
        "and eax, 8",
        "sub rsp, rax",
        // rax = the saved blob to resume -- the interrupted context
        // itself in every non-preempted case, or the next proof
        // task's blob on a genuine preemption.
        "call {dispatch}",
        "mov rsp, rax",
        "pop r15",
        "pop r14",
        "pop r13",
        "pop r12",
        "pop r11",
        "pop r10",
        "pop r9",
        "pop r8",
        "pop rbp",
        "pop rdi",
        "pop rsi",
        "pop rdx",
        "pop rcx",
        "pop rbx",
        "pop rax",
        "iretq",
        dispatch = sym timer_dispatch,
    );
}

/// The C-ABI core of `timer_entry` (its `call` target): counts the
/// tick, EOIs the PIC, and returns the saved-context pointer the asm
/// resumes from -- `trap_rsp` unchanged (the interrupted context, byte
/// for byte) in every case except a genuine preemption.
///
/// Unchanged-context paths: the interrupt came from ring 0 (switching
/// a kernel context out from inside an interrupt handler is a much
/// larger mechanism this proof deliberately does not claim), or it
/// came from ring 3 while [`PREEMPT_ARMED`] is clear -- every phase
/// except the proof, where a tick simply lands on the running task and
/// resumes it, exactly as the old `extern "x86-interrupt"` handler
/// did.
///
/// Preemption path: ring-3 code with preemption armed runs the same
/// save/next/activate/return step as `ring3_yield_dispatch`, only
/// with the clock instead of the task as the reschedule point.
///
/// # Safety
/// `trap_rsp` must be the blob `timer_entry` just built (15 saved
/// GPRs followed by the hardware frame) -- true for every call, since
/// the asm passes `rsp` right after its pushes.
unsafe extern "C" fn timer_dispatch(trap_rsp: u64) -> u64 {
    crate::idt::count_timer_tick();
    pic::end_of_interrupt(pic::IRQ_TIMER);

    // Slot 15 of the blob is RIP, slot 16 is CS -- at the same offset
    // in both the 3-slot ring-0 frame and the 5-slot ring-3 frame.
    let cs = *((trap_rsp as *const u64).add(16));
    if cs & 3 != 3 {
        return trap_rsp; // ring 0: this tick is just a tick.
    }
    if !PREEMPT_ARMED.load(Ordering::SeqCst) {
        return trap_rsp; // ring 3 outside the proof: resume it.
    }
    preempt_switch(trap_rsp)
}

/// One genuine timer preemption of the running ring-3 proof task:
/// record where it was suspended (its blob, on its own kernel stack),
/// account the switch, and return the next proof task's saved blob
/// after activating its address space -- structurally the same
/// round-robin step as `ring3_yield_dispatch`, because both operate
/// on the same [`TASKS`]/[`CURRENT`] with the same blob contract. On
/// the [`PREEMPT_TARGET`]-th switch it diverges into
/// [`preempt_finale`] instead of returning.
///
/// # Safety
/// Same contract as the step it mirrors: `trap_rsp` must be a live
/// saved blob on the current proof task's kernel stack, and `TASKS`
/// must hold exactly the built proof tasks -- guaranteed here, since
/// only [`finale`] arms preemption and it leaves exactly H and I in
/// `TASKS`.
unsafe fn preempt_switch(trap_rsp: u64) -> u64 {
    let tasks = &raw mut TASKS;
    let current = CURRENT;
    (&mut *tasks)[current].saved_trap_rsp = trap_rsp;

    HOG_PREEMPTED[current].fetch_add(1, Ordering::SeqCst);
    let switches = PREEMPT_SWITCHES.fetch_add(1, Ordering::SeqCst) + 1;
    if switches >= PREEMPT_TARGET {
        preempt_finale(); // diverges -- the Nth switch completes the proof
    }

    let next = (current + 1) % (&*tasks).len();
    CURRENT = next;
    (&*tasks)[next].address_space.activate();
    (&*tasks)[next].saved_trap_rsp
}

/// Terminal state of the timer-preemption proof. Reached from
/// [`preempt_switch`] on the [`PREEMPT_TARGET`]-th switch, running on
/// the just-preempted proof task's own guarded kernel stack with
/// interrupts still masked (timer interrupt gate). Disarms first (so
/// every later ring-3 phase just resumes from ticks, as before),
/// verifies the proof, reports, and chains into
/// `experimental::pie_demo` -- the same report-then-chain shape as
/// [`finale`].
///
/// # Safety
/// Must only run with preemption armed and exactly the two proof
/// tasks in `TASKS` -- guaranteed by being reachable only through
/// [`preempt_switch`], which only [`finale`] arms.
unsafe fn preempt_finale() -> ! {
    PREEMPT_ARMED.store(false, Ordering::SeqCst);

    let switches = PREEMPT_SWITCHES.load(Ordering::SeqCst);
    let h_off = HOG_PREEMPTED[0].load(Ordering::SeqCst);
    let i_off = HOG_PREEMPTED[1].load(Ordering::SeqCst);
    let yields_before = HOG_ARM_YIELDS.load(Ordering::SeqCst);
    let yields_now = YIELDS_TOTAL.load(Ordering::SeqCst);

    // Same technique as finale: only one address space can be active
    // at a time, so activate each proof task's space in turn and read
    // its own private spin counter.
    let tasks = &*core::ptr::addr_of!(TASKS);
    tasks[0].address_space.activate();
    let h_count = core::ptr::read_volatile(PRIVATE_ADDR as *const u64);
    tasks[1].address_space.activate();
    let i_count = core::ptr::read_volatile(PRIVATE_ADDR as *const u64);

    let names = [b'H', b'I'];
    let counts = [h_count, i_count];
    let mut counters_ok = true;
    for (&task, &count) in names.iter().zip(counts.iter()) {
        let ok = count > 0;
        counters_ok &= ok;
        serial_println!(
            "  experimental ring3-scheduler preemption self-test: task {} counter={} (>0 required -- its program contains no trap instruction, so only a timer switch could ever let it run) -- {}",
            task as char,
            count,
            if ok { "OK" } else { "FAILED" }
        );
    }

    let stats_ok = switches == PREEMPT_TARGET
        && h_off == PREEMPT_TARGET / 2
        && i_off == PREEMPT_TARGET / 2
        && yields_now == yields_before;
    serial_println!(
        "  experimental ring3-scheduler preemption self-test: task H force-switched off the CPU {} times, task I {} times ({} timer-driven switches, target {}; {} cooperative yields during the phase) -- {}",
        h_off,
        i_off,
        switches,
        PREEMPT_TARGET,
        yields_now - yields_before,
        if stats_ok { "OK" } else { "FAILED" }
    );

    let all_ok = counters_ok && stats_ok;
    serial_println!(
        "  experimental ring3-scheduler preemption self-test: two non-yielding ring-3 tasks round-robined purely by IRQ0 -- timer-driven preemption, not cooperative-only -- {}",
        if all_ok { "OK" } else { "FAILED" }
    );
    assert!(
        all_ok,
        "timer-driven ring-3 preemption self-test failed (switches={} target {}, H off {}, I off {}, H counter={}, I counter={}, yields {} -> {})",
        switches, PREEMPT_TARGET, h_off, i_off, h_count, i_count, yields_before, yields_now
    );

    let fa = (*core::ptr::addr_of_mut!(FRAME_ALLOCATOR)).take().unwrap();
    crate::experimental::pie_demo::start(fa)
}

/// Reads every task's private counter (by explicitly activating each
/// address space in turn -- necessary since only one can be active at
/// once, and none is guaranteed to be the one active when this
/// runs), checks them against [`EXPECTED_PER_TASK`], reports results,
/// then builds the two timer-preemption proof tasks, arms
/// [`PREEMPT_ARMED`], and enters the first -- a one-way transition
/// whose continuation is [`preempt_finale`], which finally chains
/// into `experimental::pie_demo::start` with the same frame allocator
/// -- keeping a single unified demo chain rather than needing
/// kernel_main to somehow run several separate one-way ring-3
/// sequences itself.
unsafe fn finale() -> ! {
    let mut counts: Vec<u64> = Vec::new();
    for i in 0..TASKS.len() {
        TASKS[i].address_space.activate();
        counts.push(core::ptr::read_volatile(PRIVATE_ADDR as *const u64));
    }

    let names = [b'E', b'F', b'G'];
    let mut all_ok = true;
    for (i, &count) in counts.iter().enumerate() {
        let ok = count == EXPECTED_PER_TASK;
        all_ok &= ok;
        serial_println!(
            "  experimental ring3-scheduler self-test: task {} counter={} (expected {}) -- {}",
            names[i] as char, count, EXPECTED_PER_TASK, if ok { "OK" } else { "MISMATCH" }
        );
    }
    if all_ok {
        serial_println!(
            "  experimental ring3-scheduler self-test: {} real trap-based yields processed, all {} tasks' full register + address-space state correctly suspended and resumed -- OK",
            TARGET_YIELDS, counts.len()
        );
    } else {
        serial_println!("  experimental ring3-scheduler self-test: FAILED");
    }

    // ---- Address-space teardown of a task that was ever active for a
    // ring-3 trap (the documented sharp edge in AddressSpace::destroy).
    //
    // Every other demo mem::forgets its spaces, so `destroy` had never
    // been exercised on a space that a ring-3 task actually ran in and
    // trapped out of. Task F did exactly that (yields 2, 5, ..., 20),
    // and is now suspended and will never be resumed -- the exact
    // precondition `destroy`'s safety comment demands. We deliberately
    // destroy F and not the task that just trapped: control is running
    // on the just-trapped task's kernel stack, so freeing *that* task's
    // stack here would free the memory we're standing on. F's stack is
    // a different allocation, so this is safe.
    let reclaimed_frames;
    {
        let fa = FRAME_ALLOCATOR.as_mut().unwrap();
        let before = fa.frames_in_use();
        let f_task = TASKS.remove(1); // task F (the middle of E, F, G)
        let f_pml4 = crate::experimental::address_space::pml4_phys_addr(&f_task.address_space);
        f_task.address_space.destroy(fa);
        let after = fa.frames_in_use();
        reclaimed_frames = before.saturating_sub(after);
        serial_println!(
            "  experimental ring3-scheduler teardown: task F's address space (pml4 {:#x}, active for its own {} trap-based yields) destroyed after suspension -- {} frames reclaimed ({} -> {} in use) -- {}",
            f_pml4.as_u64(),
            EXPECTED_PER_TASK,
            reclaimed_frames,
            before,
            after,
            if reclaimed_frames > 0 { "OK" } else { "FAILED, nothing reclaimed" }
        );
    }

    // ---- Phase 2 of this module: timer-driven preemption ----
    //
    // Everything above was cooperative: E, F, G switched only because
    // they chose to `int 0x81`. What that leaves open is exactly the
    // documented gap -- "a ring-3 task must itself yield; nothing
    // forces it to." Close it with two FRESH tasks built from a
    // program that never traps at all (userprog/prog_spin.s): H is
    // entered first with the timer armed, so the only way I ever runs
    // -- and the only way this boot ever terminates -- is IRQ0
    // force-switching the CPU away from running ring-3 code. If
    // preemption were broken the hogs would spin forever and
    // tools/qemu_boot_test.sh would time out; on top of that,
    // preempt_finale pins the exact per-task preemption counts and
    // that zero cooperative yields happened during the phase.
    let fa = (*core::ptr::addr_of_mut!(FRAME_ALLOCATOR)).as_mut().unwrap();
    let (task_h, h_entry, h_rsp) = build_ring3_task(fa, 3, true, SPIN_PROGRAM_ELF);
    let (mut task_i, _, _) = build_ring3_task(fa, 4, false, SPIN_PROGRAM_ELF);

    // Guard pages under the new live RSP0 stacks -- the same
    // structural check start() runs for E/F/G; any task's mapper sees
    // the shared guarded-stack region.
    {
        let descriptors = [
            *task_h.address_space.kernel_stack(),
            *task_i.address_space.kernel_stack(),
        ];
        let mapper = task_i.address_space.mapper();
        let names = [b'H', b'I'];
        let mut all_ok = true;
        for (i, stack) in descriptors.iter().enumerate() {
            let ok = kernel_stack::verify(&mapper, stack);
            all_ok &= ok;
            serial_println!(
                "  experimental ring3-scheduler: task {} kernel RSP0 stack {:#x}..{:#x} ({} pages) has guard page {:#x} unmapped -- {}",
                names[i] as char,
                stack.base.as_u64(),
                stack.top.as_u64(),
                stack.pages,
                stack.guard.as_u64(),
                if ok { "OK" } else { "FAILED" }
            );
        }
        serial_println!(
            "  experimental ring3-scheduler self-test: all {} preemption-proof task kernel stacks carry a genuinely unmapped guard page directly below them -- {}",
            descriptors.len(),
            if all_ok { "OK" } else { "FAILED" }
        );
    }

    // Swap the round-robin's task set to exactly [H, I]. E and G are
    // suspended forever and their TCBs forgotten rather than
    // destroyed -- deliberately leaked spaces, consistent with every
    // other demo here (F's teardown above already proved destroy()
    // works on a ring-3-active space). A 2-task set is what makes the
    // strict-alternation counts in preempt_finale exact.
    let tasks = &raw mut TASKS;
    let parked: Vec<Ring3Tcb> = core::mem::replace(&mut *tasks, Vec::new());
    core::mem::forget(parked);
    (&mut *tasks).push(task_h);
    (&mut *tasks).push(task_i);
    CURRENT = 0;

    HOG_ARM_YIELDS.store(YIELDS_TOTAL.load(Ordering::SeqCst), Ordering::SeqCst);
    PREEMPT_SWITCHES.store(0, Ordering::SeqCst);
    HOG_PREEMPTED[0].store(0, Ordering::SeqCst);
    HOG_PREEMPTED[1].store(0, Ordering::SeqCst);
    PREEMPT_ARMED.store(true, Ordering::SeqCst);
    serial_println!(
        "  experimental ring3-scheduler: timer preemption armed -- entering task H, whose program never traps; IRQ0 is the only reschedule point from here (target {} switches)",
        PREEMPT_TARGET
    );

    // Enter H for real (iretq, IF=1). Never returns: control comes
    // back only via timer_entry, and the chain resumes at pie_demo
    // from preempt_finale. Consistent with this kernel's convention,
    // failures are reported loudly rather than hidden --
    // preempt_finale asserts its own result on the way through.
    (&*tasks)[0].address_space.activate();
    usermode::enter_with_stack(h_entry.unwrap(), h_rsp.unwrap())
}

/// The actual trap entry point. See the module doc's layout diagram.
/// `#[naked]` for the same reason `experimental::scheduler::switch_to`
/// is: this function's exit is not a normal `ret` to its caller (the
/// CPU, via the `int` instruction, isn't a caller in the ABI sense),
/// and it manipulates `rsp` itself.
#[unsafe(naked)]
unsafe extern "C" fn ring3_yield_trap_entry() {
    naked_asm!(
        "push rax",
        "push rbx",
        "push rcx",
        "push rdx",
        "push rsi",
        "push rdi",
        "push rbp",
        "push r8",
        "push r9",
        "push r10",
        "push r11",
        "push r12",
        "push r13",
        "push r14",
        "push r15",
        // rdi = pointer to the full saved blob (SysV 1st arg) --
        // `call`/`ret` inside ring3_yield_dispatch are self-balancing
        // on rsp, so control returns here with rsp unchanged.
        "mov rdi, rsp",
        "call {dispatch}",
        // rax = the incoming task's saved blob pointer (SysV return value).
        "mov rsp, rax",
        "pop r15",
        "pop r14",
        "pop r13",
        "pop r12",
        "pop r11",
        "pop r10",
        "pop r9",
        "pop r8",
        "pop rbp",
        "pop rdi",
        "pop rsi",
        "pop rdx",
        "pop rcx",
        "pop rbx",
        "pop rax",
        "iretq",
        dispatch = sym ring3_yield_dispatch,
    );
}
