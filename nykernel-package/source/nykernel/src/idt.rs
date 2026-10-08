//! Kernel IDT and CPU exception handlers.
//!
//! Before this module runs, any CPU exception (a bad memory access, a
//! divide-by-zero, an invalid opcode) is unhandled -- the CPU tries to
//! deliver it, finds no valid IDT entry, faults again trying to
//! deliver *that*, and triple-faults the machine into an immediate
//! reset. This module is the difference between "the kernel prints a
//! diagnostic and halts" and "the machine silently reboots with no
//! information," which matters enormously for debugging every boot
//! phase after this one.

use crate::serial_println;
use crate::gdt::DOUBLE_FAULT_IST_INDEX;
use crate::pic;
use core::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use x86_64::structures::idt::{InterruptDescriptorTable, InterruptStackFrame, PageFaultErrorCode};
use x86_64::PrivilegeLevel;
use spin::Once;

static IDT: Once<InterruptDescriptorTable> = Once::new();

pub const SYSCALL_VECTOR: u8 = 0x80;

/// Set by `syscall_handler` (below) when it actually fires -- read back
/// by `crate::experimental::usermode`'s self-test as real, checkable
/// evidence that a ring-3 `int 0x80` reached the kernel, rather than
/// just trusting that no fault occurred.
static SYSCALL_HIT: AtomicBool = AtomicBool::new(false);
/// The CPL (0-3) the CPU reports the interrupted code was running at,
/// read from the saved `code_segment` selector's low 2 bits (the RPL
/// field, which for a correctly-loaded ring-3 code segment always
/// equals the CPL). This is the actual proof of privilege level -- not
/// an assumption that "we tried to enter ring 3, so it must have
/// worked."
static SYSCALL_CALLER_CPL: AtomicU64 = AtomicU64::new(0xFF);

pub fn syscall_was_hit() -> bool {
    SYSCALL_HIT.load(Ordering::SeqCst)
}

pub fn syscall_caller_cpl() -> u64 {
    SYSCALL_CALLER_CPL.load(Ordering::SeqCst)
}

/// Incremented once per timer interrupt. `Relaxed` is correct here:
/// this is a monotonically-increasing counter read for diagnostics/
/// coarse timing, not used to synchronize access to anything else --
/// there's no other memory operation that needs to be ordered
/// relative to a read of this value.
static TIMER_TICKS: AtomicU64 = AtomicU64::new(0);

pub fn timer_ticks() -> u64 {
    TIMER_TICKS.load(Ordering::Relaxed)
}

/// MUST be called exactly once, during [`crate::boot_phase::BootPhase::IdtInit`],
/// after [`crate::gdt::init`] (the double-fault entry's IST index only
/// means anything once the GDT/TSS that defines it is loaded). The
/// timer handler is wired here too even though the PIC itself isn't
/// remapped/unmasked until
/// [`crate::boot_phase::BootPhase::InterruptControllerInit`] runs
/// later -- IDT entries are inert until an interrupt actually arrives
/// for them, so having the handler installed early and the PIC
/// unmasked later (rather than the other way around) means there's
/// never a window where IRQ0 could fire with no handler present.
pub fn init() {
    let idt = IDT.call_once(|| {
        let mut idt = InterruptDescriptorTable::new();
        idt.divide_error.set_handler_fn(divide_error_handler);
        idt.invalid_opcode.set_handler_fn(invalid_opcode_handler);
        idt.general_protection_fault
            .set_handler_fn(general_protection_fault_handler);
        idt.page_fault.set_handler_fn(page_fault_handler);
        unsafe {
            idt.double_fault
                .set_handler_fn(double_fault_handler)
                .set_stack_index(DOUBLE_FAULT_IST_INDEX);
        }
        let timer_vector = (pic::PIC1_OFFSET + pic::IRQ_TIMER) as usize;
        idt[timer_vector].set_handler_fn(timer_interrupt_handler);

        // The RTC's periodic interrupt (IRQ8, slave PIC) -- the
        // kernel's second real hardware IRQ line, proven by
        // `crate::rtc`'s self-test. Vector 0x28 (PIC2_OFFSET + 0).
        let rtc_vector = (pic::PIC2_OFFSET + (crate::rtc::IRQ_RTC - 8)) as usize;
        idt[rtc_vector].set_handler_fn(rtc_interrupt_handler);

        // Ring 3 code must be explicitly permitted to `int 0x80` -- an
        // IDT gate's default DPL is 0, so a ring-3 `int` to a gate
        // that's still DPL 0 is itself a GPF (the CPU checks the
        // *gate's* privilege level against CPL, not just whether a
        // handler exists). This is the one interrupt vector in this
        // kernel a ring-3 program is allowed to invoke directly.
        idt[SYSCALL_VECTOR as usize]
            .set_handler_fn(syscall_handler)
            .set_privilege_level(PrivilegeLevel::Ring3);

        // A second ring-3-callable vector, dedicated to
        // experimental::ring3_scheduler's real trap-based task
        // switching -- kept separate from SYSCALL_VECTOR (which
        // usermode/isolation_demo use for one-shot report-and-halt
        // traps) so the two mechanisms can't be confused with each
        // other. Installed via a raw handler address rather than
        // `set_handler_fn` because that function's calling convention
        // is deliberately not `extern "x86-interrupt"` -- see that
        // module's doc comment for why.
        crate::experimental::ring3_scheduler::install(&mut idt);

        // A third ring-3-callable vector, dedicated to
        // experimental::pie_demo's one-shot report-and-chain trap --
        // uses the ordinary `extern "x86-interrupt"` ABI (like
        // SYSCALL_VECTOR, not ring3_scheduler's raw handler), since
        // this demo only needs to report a result once, not save full
        // register state for a later resume.
        idt[crate::experimental::pie_demo::TRAP_VECTOR as usize]
            .set_handler_fn(pie_trap_handler)
            .set_privilege_level(PrivilegeLevel::Ring3);

        // A fourth ring-3-callable vector, dedicated to
        // experimental::pe_demo -- the first genuine Windows-format
        // (PE32+) loading proof, same simple report-once ABI as
        // pie_trap_handler.
        idt[crate::experimental::pe_demo::TRAP_VECTOR as usize]
            .set_handler_fn(pe_trap_handler)
            .set_privilege_level(PrivilegeLevel::Ring3);

        // A fifth ring-3-callable vector, dedicated to
        // experimental::pe_reloc_demo -- proves PE base relocations by
        // loading a relocatable PE32+ binary at a loader-chosen
        // address, same simple report-once ABI as the others.
        idt[crate::experimental::pe_reloc_demo::TRAP_VECTOR as usize]
            .set_handler_fn(pe_reloc_trap_handler)
            .set_privilege_level(PrivilegeLevel::Ring3);
        idt
    });
    idt.load();
}

extern "x86-interrupt" fn divide_error_handler(stack_frame: InterruptStackFrame) {
    serial_println!("EXCEPTION: DIVIDE ERROR\n{:#?}", stack_frame);
    crate::hlt_loop();
}

extern "x86-interrupt" fn invalid_opcode_handler(stack_frame: InterruptStackFrame) {
    serial_println!("EXCEPTION: INVALID OPCODE\n{:#?}", stack_frame);
    crate::hlt_loop();
}

extern "x86-interrupt" fn general_protection_fault_handler(
    stack_frame: InterruptStackFrame,
    error_code: u64,
) {
    serial_println!(
        "EXCEPTION: GENERAL PROTECTION FAULT (error code: {:#x})\n{:#?}",
        error_code,
        stack_frame
    );
    crate::hlt_loop();
}

extern "x86-interrupt" fn page_fault_handler(
    stack_frame: InterruptStackFrame,
    error_code: PageFaultErrorCode,
) {
    use x86_64::registers::control::Cr2;
    let accessed = Cr2::read();

    // The guard-page overflow proof (`memory::guard_demo::run_and_prove`)
    // deliberately faults as the last act of the boot, so that a guard
    // page's enforcement is proven by a real fault rather than asserted.
    // When that probe is armed, this fault is expected and its outcome is
    // decided by guard_demo (success on the exact guard-page write,
    // failure otherwise) instead of the generic halt below.
    if crate::memory::guard_demo::is_armed() {
        crate::memory::guard_demo::fatal_page_fault(accessed, error_code)
    }

    serial_println!(
        "EXCEPTION: PAGE FAULT\naccessed address: {:?}\nerror code: {:?}\n{:#?}",
        accessed,
        error_code,
        stack_frame
    );
    crate::hlt_loop();
}

extern "x86-interrupt" fn timer_interrupt_handler(_stack_frame: InterruptStackFrame) {
    TIMER_TICKS.fetch_add(1, Ordering::Relaxed);
    pic::end_of_interrupt(pic::IRQ_TIMER);
}

/// IRQ8 (RTC periodic). `crate::rtc::handle` reads the RTC's register C
/// to deassert its interrupt line, then counts the tick; the EOI here
/// acknowledges it at the slave *and* master PICs (`end_of_interrupt`
/// handles the cascade for IRQ8-15).
extern "x86-interrupt" fn rtc_interrupt_handler(_stack_frame: InterruptStackFrame) {
    crate::rtc::handle();
    pic::end_of_interrupt(crate::rtc::IRQ_RTC);
}

/// The kernel side of `crate::experimental::usermode`'s ring-3 proof
/// of concept. Deliberately minimal: this doesn't implement a real
/// syscall ABI (no argument passing via registers -- the
/// `extern "x86-interrupt"` calling convention the `x86_64` crate
/// exposes doesn't give handler code direct access to the interrupted
/// context's general-purpose registers, only the architectural
/// interrupt frame). What it *can* prove, and does: that ring-3 code
/// really executed (via `stack_frame.code_segment`'s saved RPL, which
/// the CPU itself set on trap entry -- not something software could
/// fake) and that the trap back into ring 0 completed without
/// faulting.
///
/// This handler is also, deliberately, this proof's finale-and-relay:
/// since `usermode::enter()` is a one-way transfer into ring 3 (see
/// its own doc comment for why), this handler -- not `kernel_main` --
/// is the only kernel code guaranteed to run after each ring-3 stage.
/// All it does itself is record the CPU-reported CPL (real evidence,
/// not assumed); the actual multi-stage orchestration (single trap vs.
/// the two-process isolation sequence) lives in
/// `experimental::isolation_demo`, which this delegates to entirely.
extern "x86-interrupt" fn syscall_handler(stack_frame: InterruptStackFrame) {
    let cpl = stack_frame.code_segment as u64 & 0x3;
    SYSCALL_CALLER_CPL.store(cpl, Ordering::SeqCst);
    SYSCALL_HIT.store(true, Ordering::SeqCst);

    unsafe {
        crate::experimental::isolation_demo::on_syscall_trap(cpl);
    }
    // on_syscall_trap either diverges (reports results and halts/exits,
    // the expected path for this kernel's current demo sequence) or
    // returns false if the demo isn't active; either way there's
    // nothing left for this handler to do.
}

/// The one trap `experimental::pie_demo`'s program issues -- reports
/// its result and chains into `experimental::pe_demo`, same finale
/// pattern as `experimental::ring3_scheduler`'s.
extern "x86-interrupt" fn pie_trap_handler(_stack_frame: InterruptStackFrame) {
    unsafe {
        crate::experimental::pie_demo::on_trap();
    }
}

/// The one trap `experimental::pe_demo`'s program issues -- reports
/// its result and chains into `experimental::isolation_demo`, the
/// final link in this kernel's demo chain.
extern "x86-interrupt" fn pe_trap_handler(_stack_frame: InterruptStackFrame) {
    unsafe {
        crate::experimental::pe_demo::on_trap();
    }
}

/// The one trap `experimental::pe_reloc_demo`'s program issues -- reports
/// the base-relocation result and chains into `experimental::isolation_demo`,
/// the final link in this kernel's demo chain.
extern "x86-interrupt" fn pe_reloc_trap_handler(_stack_frame: InterruptStackFrame) {
    unsafe {
        crate::experimental::pe_reloc_demo::on_trap();
    }
}

/// Runs on its own dedicated stack (see gdt.rs) precisely so that a
/// double fault caused by kernel stack exhaustion is still handled
/// instead of triple-faulting. A double-fault handler that returns
/// normally is a bug -- x86_64's double-fault ABI does not define
/// recovery -- so this one halts unconditionally after logging.
extern "x86-interrupt" fn double_fault_handler(
    stack_frame: InterruptStackFrame,
    _error_code: u64,
) -> ! {
    serial_println!("EXCEPTION: DOUBLE FAULT\n{:#?}", stack_frame);
    crate::hlt_loop();
}
