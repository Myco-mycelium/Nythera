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
use x86_64::{PrivilegeLevel, VirtAddr};
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
        // x86_64 0.15 removed Index<usize> from the IDT; vector numbers
        // index with u8 now.
        let timer_vector = (pic::PIC1_OFFSET + pic::IRQ_TIMER) as u8;
        // The timer (IRQ0, vector 0x20) is this kernel's one interrupt
        // handler that is NOT `extern "x86-interrupt"`: preempting a
        // ring-3 task mid-instruction means saving and later restoring
        // every general-purpose register around the switch, which the
        // x86-interrupt ABI's architectural frame (RIP/CS/RFLAGS/RSP/SS)
        // alone cannot do. `experimental::ring3_scheduler::timer_entry`
        // is a raw `#[naked]` handler that counts the tick and EOIs
        // exactly as the old handler did, and resumes the interrupted
        // context byte-for-byte unchanged in every case except a
        // genuine timer preemption of ring-3 task code (its dedicated
        // proof phase, `PREEMPT_ARMED`). Installed via `set_handler_addr`
        // because the handler's ABI is deliberately not the
        // x86-interrupt convention -- same reasoning as `install`'s
        // `int 0x81` gate below.
        unsafe {
            idt[timer_vector].set_handler_addr(VirtAddr::new(
                crate::experimental::ring3_scheduler::timer_entry as *const () as u64,
            ));
        }

        // The RTC's periodic interrupt (IRQ8, slave PIC) -- the
        // kernel's second real hardware IRQ line, proven by
        // `crate::rtc`'s self-test. Vector 0x28 (PIC2_OFFSET + 0).
        let rtc_vector = (pic::PIC2_OFFSET + (crate::rtc::IRQ_RTC - 8)) as u8;
        idt[rtc_vector].set_handler_fn(rtc_interrupt_handler);

        // The PS/2 keyboard (IRQ1, master PIC) -- a real input IRQ line
        // with a real handler, proven input-free by `crate::keyboard`'s
        // 8042 output-buffer injection self-test. Vector 0x21.
        let keyboard_vector = (pic::PIC1_OFFSET + crate::keyboard::IRQ_KEYBOARD) as u8;
        idt[keyboard_vector].set_handler_fn(keyboard_interrupt_handler);

        // The ATA/ATAPI IDE channels (IRQ14 primary, IRQ15 secondary,
        // both on the slave PIC so their vectors follow PIC2_OFFSET).
        // Real handlers are installed here before `crate::ata` ever
        // unmasks a line; it unmasks only the channel that actually has
        // a device.
        let ata_primary_vector =
            (pic::PIC2_OFFSET + (crate::ata::IRQ_ATA_PRIMARY - 8)) as u8;
        idt[ata_primary_vector].set_handler_fn(ata_primary_interrupt_handler);
        let ata_secondary_vector =
            (pic::PIC2_OFFSET + (crate::ata::IRQ_ATA_SECONDARY - 8)) as u8;
        idt[ata_secondary_vector].set_handler_fn(ata_secondary_interrupt_handler);

        // Ring 3 code must be explicitly permitted to `int 0x80` -- an
        // IDT gate's default DPL is 0, so a ring-3 `int` to a gate
        // that's still DPL 0 is itself a GPF (the CPU checks the
        // *gate's* privilege level against CPL, not just whether a
        // handler exists). This is the one interrupt vector in this
        // kernel a ring-3 program is allowed to invoke directly.
        idt[SYSCALL_VECTOR as u8]
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
        idt[crate::experimental::pie_demo::TRAP_VECTOR as u8]
            .set_handler_fn(pie_trap_handler)
            .set_privilege_level(PrivilegeLevel::Ring3);

        // A fourth ring-3-callable vector, dedicated to
        // experimental::pe_demo -- the first genuine Windows-format
        // (PE32+) loading proof, same simple report-once ABI as
        // pie_trap_handler.
        idt[crate::experimental::pe_demo::TRAP_VECTOR as u8]
            .set_handler_fn(pe_trap_handler)
            .set_privilege_level(PrivilegeLevel::Ring3);

        // A fifth ring-3-callable vector, dedicated to
        // experimental::pe_reloc_demo -- proves PE base relocations by
        // loading a relocatable PE32+ binary at a loader-chosen
        // address, same simple report-once ABI as the others.
        idt[crate::experimental::pe_reloc_demo::TRAP_VECTOR as u8]
            .set_handler_fn(pe_reloc_trap_handler)
            .set_privilege_level(PrivilegeLevel::Ring3);

        // A sixth ring-3-callable vector, dedicated to
        // experimental::elf_dyn_demo -- the first shared-object
        // (real `ld -shared` .so) loading proof with symbol-resolved
        // relocations, same simple report-once ABI as the others.
        idt[crate::experimental::elf_dyn_demo::TRAP_VECTOR as u8]
            .set_handler_fn(elf_dyn_trap_handler)
            .set_privilege_level(PrivilegeLevel::Ring3);

        // A seventh ring-3-callable vector, dedicated to
        // experimental::elf_dyn_cross_demo -- the cross-object
        // symbol-resolution proof (a dependent .so whose UNDEF
        // GLOB_DAT resolves against the registry-held provider .so).
        idt[crate::experimental::elf_dyn_cross_demo::TRAP_VECTOR as u8]
            .set_handler_fn(elf_dyn_cross_trap_handler)
            .set_privilege_level(PrivilegeLevel::Ring3);

        // The eighth ring-3-callable vector: the SHARED lazy-bind
        // service -- loader-armed trampoline stubs trap here on a
        // JUMP_SLOT's first use; the SHARED lazy_binder service
        // identifies the owning object by its trampoline page, so
        // EVERY lazy-armed dependent in a boot binds through this one
        // gate. Resolves through the same registry + version contract
        // and PATCHES the slot, then returns (it does not chain).
        idt[crate::experimental::elf::LAZY_BIND_VECTOR as u8]
            .set_handler_fn(elf_lazy_bind_handler)
            .set_privilege_level(PrivilegeLevel::Ring3);

        // The eleventh ring-3-callable vector: the OWN-.plt GNU
        // resolver gate -- an own-`.plt`-armed object's `.got.plt`[2]
        // thunk traps `int 0x8B` here on a JUMP_SLOT's first use, with
        // the REAL linker-protocol spill (PLT1's index, PLT0's
        // link-map cookie) on the user stack. Installed as a RAW
        // handler address (like the timer IRQ -- the same reasoning:
        // this gate never exits through the x86-interrupt ABI),
        // because the resolver GUIDES its return straight into the
        // resolved callee with EVERY user GPR restored -- GNU
        // _dl_runtime_resolve's shape, implemented in
        // experimental::lazy_binder::plt_resolver_raw.
        unsafe {
            idt[crate::experimental::elf::PLT_RESOLVE_VECTOR as u8]
                .set_handler_addr(VirtAddr::new(
                    crate::experimental::lazy_binder::plt_resolver_raw as *const () as u64,
                ))
                .set_privilege_level(PrivilegeLevel::Ring3);
        }

        // A ninth ring-3-callable vector, dedicated to
        // experimental::elf_dyn_full_demo -- the DT_NEEDED + version
        // contract + lazy-binding proof.
        idt[crate::experimental::elf_dyn_full_demo::TRAP_VECTOR as u8]
            .set_handler_fn(elf_dyn_full_trap_handler)
            .set_privilege_level(PrivilegeLevel::Ring3);

        // A tenth ring-3-callable vector, dedicated to
        // experimental::elf_dyn_deps_demo -- the recursive DT_NEEDED
        // catalog, interposition, and weak-version proof.
        idt[crate::experimental::elf_dyn_deps_demo::TRAP_VECTOR as u8]
            .set_handler_fn(elf_dyn_deps_trap_handler)
            .set_privilege_level(PrivilegeLevel::Ring3);

        // A twelfth ring-3-callable vector, dedicated to
        // experimental::elf_dyn_plt_demo -- the OWN-.plt GNU lazy
        // protocol proof.
        idt[crate::experimental::elf_dyn_plt_demo::TRAP_VECTOR as u8]
            .set_handler_fn(elf_dyn_plt_demo_trap_handler)
            .set_privilege_level(PrivilegeLevel::Ring3);

        // A thirteenth ring-3-callable vector, dedicated to
        // experimental::pe_import_demo -- the PE32+ import
        // resolution proof (dedicated 0x8D: the plt-demo's completion
        // gate owns 0x8C, and registering over it is what the first
        // failed boot proved) (a real Windows-format dependent whose real
        // import directory resolves against a real Windows-format
        // DLL's real export directory).
        idt[crate::experimental::pe_import_demo::TRAP_VECTOR as u8]
            .set_handler_fn(pe_import_trap_handler)
            .set_privilege_level(PrivilegeLevel::Ring3);
        // A fourteenth ring-3-callable vector, dedicated to
        // experimental::pe_exception_demo -- the PE32+ exception-table
        // (.pdata) proof (new 0x8E, distinct from every other demo's
        // vector; the 0x8D lesson from the first failed import boot is
        // the standing rule: registering over another demo's vector is
        // a bug, not a shortcut).
        idt[crate::experimental::pe_exception_demo::TRAP_VECTOR as u8]
            .set_handler_fn(pe_exception_trap_handler)
            .set_privilege_level(PrivilegeLevel::Ring3);
        // A fifteenth ring-3-callable vector, dedicated to
        // experimental::pe_resource_demo -- the PE32+ resource-tree
        // proof (new 0x8F, distinct from every other demo's vector;
        // same standing rule as 0x8D's lesson).
        idt[crate::experimental::pe_resource_demo::TRAP_VECTOR as u8]
            .set_handler_fn(pe_resource_trap_handler)
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
    // Cr2::read returns a typed Result since x86_64 0.15 (the error case
    // is a non-canonical stored CR2). For a delivered page fault the CPU
    // itself loads CR2, so the Rx path is unreachable in practice --
    // panic loudly if it ever happens rather than guessing an address.
    let accessed = Cr2::read()
        .expect("CR2 delivered a non-canonical faulting address");

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

/// Counts one IRQ0 tick. Called by
/// `experimental::ring3_scheduler::timer_entry` on *every* timer
/// interrupt -- ring 0, ring 3, preempted or not -- so
/// [`timer_ticks`] keeps exactly the meaning it had when an
/// `extern "x86-interrupt"` handler did this inline (the EOI that
/// used to accompany it now lives in that entry, immediately after
/// this call, on every path).
pub(crate) fn count_timer_tick() {
    TIMER_TICKS.fetch_add(1, Ordering::Relaxed);
}

/// IRQ8 (RTC periodic). `crate::rtc::handle` reads the RTC's register C
/// to deassert its interrupt line, then counts the tick; the EOI here
/// acknowledges it at the slave *and* master PICs (`end_of_interrupt`
/// handles the cascade for IRQ8-15).
extern "x86-interrupt" fn rtc_interrupt_handler(_stack_frame: InterruptStackFrame) {
    crate::rtc::handle();
    pic::end_of_interrupt(crate::rtc::IRQ_RTC);
}

/// IRQ1 (PS/2 keyboard). `crate::keyboard::handle` reads the scancode
/// out of the controller's output buffer (which deasserts its IRQ
/// line) and counts it; IRQ1 is a master-PIC line, so a single EOI is
/// all that's needed.
extern "x86-interrupt" fn keyboard_interrupt_handler(_stack_frame: InterruptStackFrame) {
    crate::keyboard::handle();
    pic::end_of_interrupt(crate::keyboard::IRQ_KEYBOARD);
}

/// IRQ14 (primary IDE channel). `crate::ata::handle` acknowledges the
/// device (reading its status register deasserts the line), drains any
/// pending IDENTIFY data, and counts the interrupt; IRQ14/15 are on the
/// slave PIC, so `end_of_interrupt` EOIs both PICs.
extern "x86-interrupt" fn ata_primary_interrupt_handler(_stack_frame: InterruptStackFrame) {
    crate::ata::handle(0);
    pic::end_of_interrupt(crate::ata::IRQ_ATA_PRIMARY);
}

/// IRQ15 (secondary IDE channel).
extern "x86-interrupt" fn ata_secondary_interrupt_handler(_stack_frame: InterruptStackFrame) {
    crate::ata::handle(1);
    pic::end_of_interrupt(crate::ata::IRQ_ATA_SECONDARY);
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
    // x86_64 0.15 exposes the frame's code segment as a SegmentSelector
    // instead of a raw u16; the caller's CPL is the selector's RPL field.
    let cpl = stack_frame.code_segment.rpl() as u64;
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

/// The one trap `experimental::pe_import_demo`'s client program issues
/// (`int $0x8D` from ring 3) -- reports the import-resolution results
/// (data read through a resolved IAT slot, return value through an
/// indirect call through another) and chains into
/// `experimental::isolation_demo`.
extern "x86-interrupt" fn pe_import_trap_handler(_stack_frame: InterruptStackFrame) {
    unsafe {
        crate::experimental::pe_import_demo::on_trap();
    }
}

/// The one trap `experimental::pe_exception_demo`'s good carrier's body
/// issues (`int $0x8e` from ring 3) -- reports whether the composed
/// carrier's verified-exception-table load really ran the client's own
/// code, then chains into `experimental::pe_resource_demo` (the
/// resource-tree proof), which reaches `experimental::isolation_demo`.
extern "x86-interrupt" fn pe_exception_trap_handler(_stack_frame: InterruptStackFrame) {
    unsafe {
        crate::experimental::pe_exception_demo::on_trap();
    }
}

/// The one trap `experimental::pe_resource_demo`'s good carrier's body
/// issues (`int $0x8f` from ring 3) -- reports whether the composed
/// carrier's verified-resource-tree load really ran the client's own
/// code, then chains into `experimental::isolation_demo`, keeping the
/// single unified demo chain linear.
extern "x86-interrupt" fn pe_resource_trap_handler(_stack_frame: InterruptStackFrame) {
    unsafe {
        crate::experimental::pe_resource_demo::on_trap();
    }
}

/// The one trap `experimental::elf_dyn_demo`'s program (the shared
/// object's `dyn_payload`) issues -- reports the by-name-lookup /
/// symbol-resolution result and chains into `experimental::pe_demo`.
extern "x86-interrupt" fn elf_dyn_trap_handler(_stack_frame: InterruptStackFrame) {
    unsafe {
        crate::experimental::elf_dyn_demo::on_trap();
    }
}

/// The one trap `experimental::elf_dyn_cross_demo`'s program (the
/// dependent .so's `cross_payload`) issues -- reports the
/// cross-resolved GOT dereference result and chains into
/// `experimental::pe_demo`.
extern "x86-interrupt" fn elf_dyn_cross_trap_handler(_stack_frame: InterruptStackFrame) {
    unsafe {
        crate::experimental::elf_dyn_cross_demo::on_trap();
    }
}

/// The shared lazy-bind service: the loader-armed stub traps here on
/// a PLT entry's first use; the SHARED lazy_binder service owns
/// per-object dispatch (by trampoline page), resolution + patching
/// happens inside it, and the handler RETURNS (never chains).
extern "x86-interrupt" fn elf_lazy_bind_handler(stack_frame: InterruptStackFrame) {
    unsafe {
        crate::experimental::lazy_binder::service(stack_frame);
    }
}

/// The one trap `experimental::elf_dyn_full_demo`'s program (the
/// versioned dependent's `all_payload`) issues -- verifies the
/// cross_value + lazy-binding facts and chains into
/// `experimental::elf_dyn_deps_demo`.
extern "x86-interrupt" fn elf_dyn_full_trap_handler(_stack_frame: InterruptStackFrame) {
    unsafe {
        crate::experimental::elf_dyn_full_demo::on_trap();
    }
}

/// The one trap `experimental::elf_dyn_deps_demo`'s program (the
/// catalog-dependent's `deps_payload`) issues -- verifies the
/// catalog/interposition/weak facts and chains into
/// `experimental::pe_demo`.
extern "x86-interrupt" fn elf_dyn_deps_trap_handler(_stack_frame: InterruptStackFrame) {
    unsafe {
        crate::experimental::elf_dyn_deps_demo::on_trap();
    }
}

/// The one trap `experimental::elf_dyn_plt_demo`'s program (the
/// own-`.plt` GNU-protocol dependent's `plt_payload`) issues --
/// verifies the GNU lazy-resolution facts and chains into
/// `experimental::pe_demo`.
extern "x86-interrupt" fn elf_dyn_plt_demo_trap_handler(_stack_frame: InterruptStackFrame) {
    unsafe {
        crate::experimental::elf_dyn_plt_demo::on_trap();
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
