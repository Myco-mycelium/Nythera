//! Ring 3 (user mode) transition and minimal ELF loading -- a real,
//! verified prerequisite for Phase 5/6 (Windows/Android compatibility),
//! which fundamentally cannot exist until *something* in this kernel
//! can load and run application code at CPL 3 rather than CPL 0. See
//! `crate::experimental`'s module-level banner: like the scheduler and
//! IPC proofs, this is not NPS-002/NPS-003 conformance -- there is no
//! per-process address space (loaded code runs in the *same* page
//! tables as the kernel, just at a lower privilege level and only on
//! pages explicitly marked `USER_ACCESSIBLE`), no relocations/dynamic
//! linking (see `crate::experimental::elf`), and no real syscall ABI
//! (see the comment on `idt::syscall_handler` for exactly what's
//! missing there).
//!
//! What this genuinely proves, checkably: (1) the GDT's user code/data
//! segments and the TSS's ring-0 stack (RSP0) are correctly configured
//! -- if they weren't, entering ring 3 or trapping back out of it would
//! fault, and this kernel's existing exception handlers would report
//! that clearly rather than the machine silently resetting; (2) a
//! page explicitly marked `USER_ACCESSIBLE` really is executable from
//! ring 3, and by implication a page that *isn't* marked that way
//! would not be (the flag is doing real work, not decorative -- proven
//! by an actual negative-control test, see IMPLEMENTATION_STATUS.md);
//! (3) an `int 0x80` gate with `DPL=Ring3` really is callable from
//! ring 3 and delivers correctly into a ring-0 handler; (4) a real,
//! separately-built ELF64 file's headers can be parsed and its code
//! actually executed -- verified by checking a value that file's own
//! code computed and wrote, not by the trap alone.

use core::arch::naked_asm;
use x86_64::structures::paging::{OffsetPageTable, PageTableFlags};
use x86_64::VirtAddr;

use crate::experimental::elf;
use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::memory::paging;
use crate::{gdt, idt};

/// Where the test program (`userprog/prog.s`, embedded below) expects
/// to be loaded -- must match `userprog/linker.ld`'s load address,
/// since this loader doesn't support relocating a non-PIE executable.
/// The ELF loader itself doesn't assume this constant; it reads
/// `p_vaddr` from the file. This constant exists only so the stack
/// address below doesn't collide with wherever the code segment lands.
pub const USER_CODE_ADDR: u64 = 0x_2000_0000_000;
pub const USER_STACK_ADDR: u64 = USER_CODE_ADDR + 0x10_000;

/// A real, separately-built ELF64 executable (see
/// `userprog/prog.s` + `userprog/linker.ld`, built by `build.sh`
/// before the kernel crate so this file exists at compile time) --
/// not hand-assembled bytes matching whatever the loader expects. Its
/// entry point writes a magic value to `USER_STACK_ADDR` (the already-
/// mapped user stack page -- see the comment in `prog.s` for why it
/// reuses that mapping rather than requiring a second segment) and
/// traps via `int 0x80`, so the kernel can verify the *file's own
/// code* actually ran, not merely that some code reached the syscall
/// gate.
static USER_PROGRAM_ELF: &[u8] = include_bytes!("../../userprog/userprog.elf");

/// The value `userprog/prog.s` writes to `USER_STACK_ADDR` -- checked
/// by `kernel_main` after the trap as this proof's actual correctness
/// evidence (see the module doc above).
pub const EXPECTED_MAGIC: u64 = 0xC0FFEE;

#[derive(Debug)]
pub enum UsermodeError {
    Elf(elf::ElfError),
    StackMappingFailed,
}

/// Loads [`USER_PROGRAM_ELF`] via `experimental::elf::load` (mapping
/// whatever `PT_LOAD` segments the file's own headers describe -- not
/// a hardcoded assumption about its layout) and separately maps the
/// user stack page (`USER_ACCESSIBLE`, required -- without it, any
/// ring-3 access would fault regardless of the GDT/TSS setup being
/// otherwise correct). Returns the entry point the ELF file itself
/// specifies. Must run before [`enter`].
pub fn prepare(
    mapper: &mut OffsetPageTable<'static>,
    frame_allocator: &mut BumpFrameAllocator,
) -> Result<VirtAddr, UsermodeError> {
    prepare_elf(USER_PROGRAM_ELF, mapper, frame_allocator)
}

/// The general form of [`prepare`], taking arbitrary ELF bytes --
/// used by `experimental::address_space`'s two-process isolation demo
/// to load a *different* program (`userprog/prog_b.s`) into a
/// *different* address space, while [`prepare`] stays a simple,
/// unchanged single-argument entry point for the original one-process
/// proof.
pub fn prepare_elf(
    elf_bytes: &[u8],
    mapper: &mut OffsetPageTable<'static>,
    frame_allocator: &mut BumpFrameAllocator,
) -> Result<VirtAddr, UsermodeError> {
    let loaded = elf::load(elf_bytes, mapper, frame_allocator, 0).map_err(UsermodeError::Elf)?;

    let stack_flags = PageTableFlags::PRESENT | PageTableFlags::WRITABLE | PageTableFlags::USER_ACCESSIBLE | PageTableFlags::NO_EXECUTE;
    let stack_mapped = paging::map_range(mapper, frame_allocator, VirtAddr::new(USER_STACK_ADDR), 1, stack_flags);
    if stack_mapped != 1 {
        return Err(UsermodeError::StackMappingFailed);
    }

    Ok(loaded.entry_point)
}

/// Transitions to ring 3 at `entry_point` (as returned by [`prepare`])
/// and never returns to its caller in the normal sense -- `iretq` is a
/// one-way privilege transfer. Control only comes back to ring 0 via a
/// trap (the ring-3 program's `int 0x80`), and a trap handler resumes
/// wherever it interrupted (ring 3), not back here. This is
/// deliberately called as `kernel_main`'s own final action (see
/// `lib.rs`), not run as an `experimental::scheduler` task -- a task
/// built around this could never `yield_task()` back, breaking that
/// scheduler's round-robin invariant. `idt::syscall_handler` is where
/// this proof's actual reporting and shutdown happen, since that's the
/// only kernel code guaranteed to run after this point.
///
/// # Safety
/// [`prepare`] must have succeeded first, and `entry_point` must be
/// the value it returned. Must only be called once -- there is no
/// support for re-entering or nesting this.
pub unsafe fn enter(entry_point: VirtAddr) -> ! {
    enter_with_stack(entry_point, VirtAddr::new(USER_STACK_ADDR + 4096 - 16))
}

/// As [`enter`], but with an explicit user-mode stack pointer instead
/// of assuming [`USER_STACK_ADDR`] -- needed by
/// `experimental::ring3_scheduler`, whose tasks each have their own
/// private stack mapped at their own address, not this module's.
///
/// # Safety
/// Same as [`enter`], plus: `user_rsp` must point into a page mapped
/// `PRESENT | WRITABLE | USER_ACCESSIBLE` in the currently active
/// address space.
pub unsafe fn enter_with_stack(entry_point: VirtAddr, user_rsp: VirtAddr) -> ! {
    let user_cs = gdt::user_code_selector().0 as u64 | 3; // RPL = 3
    let user_ss = gdt::user_data_selector().0 as u64 | 3;
    let rflags: u64 = 0x200; // IF=1 -- ring 3 shouldn't run with interrupts masked

    enter_usermode(entry_point.as_u64(), user_cs, rflags, user_rsp.as_u64(), user_ss);
}

/// # Safety
/// See [`enter`]. Never returns; transfers control via `iretq`.
#[unsafe(naked)]
unsafe extern "C" fn enter_usermode(
    user_rip: u64,
    user_cs: u64,
    rflags: u64,
    user_rsp: u64,
    user_ss: u64,
) -> ! {
    naked_asm!(
        // Data segment registers aren't touched by iretq -- set them
        // to the user data selector explicitly so ring-3 code doesn't
        // inherit ring-0 segment state. (r9 is a scratch register the
        // SysV ABI guarantees is caller-saved and not one of our 5
        // incoming arguments, so it's safe to clobber here.)
        "mov r9, r8",       // r8 holds user_ss (5th arg); stash before it's overwritten below
        "mov ds, r9w",
        "mov es, r9w",
        "mov fs, r9w",
        "mov gs, r9w",
        // Build the iretq frame. iretq pops, in order: RIP, CS, RFLAGS,
        // RSP, SS -- so push in the reverse order.
        "push r8",   // ss
        "push rcx",  // rsp
        "push rdx",  // rflags
        "push rsi",  // cs
        "push rdi",  // rip
        "iretq",
    );
}

/// Polled by `kernel_main`'s own final self-test check -- NOT run via
/// `crate::experimental::scheduler`. That was the original design for
/// this module and it doesn't work: `enter()` is one-way (`iretq`
/// transfers control to ring 3 permanently; the only way back into
/// ring 0 is a trap, and a trap handler returns to wherever it
/// interrupted -- ring 3 -- not back to whichever kernel task called
/// `enter()`). A scheduler task built around `enter()` could never
/// `yield_task()` back, which would silently break the round-robin
/// math every other `experimental` demo depends on. So instead:
/// `enter()` is `kernel_main`'s own last action, and the syscall
/// handler itself (which *does* keep running in ring 0 after the trap)
/// is what reports final results and halts -- see `idt::syscall_handler`.
pub fn syscall_observed() -> bool {
    idt::syscall_was_hit()
}

pub fn syscall_caller_cpl() -> u64 {
    idt::syscall_caller_cpl()
}
