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

use alloc::vec::Vec;
use core::arch::naked_asm;
use core::sync::atomic::{AtomicU32, Ordering};
use x86_64::structures::idt::InterruptDescriptorTable;
use x86_64::structures::paging::PageTableFlags;
use x86_64::{PrivilegeLevel, VirtAddr};

use crate::experimental::address_space::AddressSpace;
use crate::experimental::{elf, usermode};
use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::memory::kernel_stack;
use crate::memory::paging;
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
) -> (Ring3Tcb, Option<VirtAddr>, Option<VirtAddr>) {
    let mut space = AddressSpace::new_sharing_kernel(frame_allocator)
        .unwrap_or_else(|_| panic!("failed to build ring3 task {} address space", id));
    unsafe { space.activate() };
    let mut mapper = space.mapper();

    let loaded = elf::load(YIELD_PROGRAM_ELF, &mut mapper, frame_allocator, 0)
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

    let (task_e, e_entry, e_rsp) = build_ring3_task(fa, 0, true);
    let (task_f, _, _) = build_ring3_task(fa, 1, false);
    let (task_g, _, _) = build_ring3_task(fa, 2, false);

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

/// Reads every task's private counter (by explicitly activating each
/// address space in turn -- necessary since only one can be active at
/// once, and none is guaranteed to be the one active when this
/// runs), checks them against [`EXPECTED_PER_TASK`], reports results,
/// and chains into `experimental::pie_demo::start` with the same frame
/// allocator rather than exiting/halting directly -- keeping a single
/// unified demo chain rather than needing kernel_main to somehow run
/// several separate one-way ring-3 sequences itself.
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

    // Chains into pie_demo regardless of this demo's own result --
    // consistent with this kernel's convention of reporting a failure
    // and continuing to gather as much diagnostic information as
    // possible in a single boot, rather than halting at the first sign
    // of trouble. Every demo in the chain has its own independent
    // self-test and reports its own result regardless of any other's.
    let fa = FRAME_ALLOCATOR.take().unwrap();
    crate::experimental::pie_demo::start(fa)
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
