//! Kernel-owned GDT + TSS.
//!
//! boot.s's `gdt64` got the CPU into long mode and is never touched
//! again after this module runs (per [`crate::boot_phase::BootPhase::GdtInit`]).
//! The reason a *second* GDT is needed at all, rather than just
//! reusing boot.s's: a Task State Segment descriptor can only live in
//! a GDT, and the TSS is how we get a guaranteed-valid stack for the
//! double-fault handler specifically (via the Interrupt Stack Table --
//! IST). Without a dedicated IST stack, a double fault caused by a
//! stack overflow (kernel stack guard page hit -> page fault -> the
//! page-fault handler itself needs stack space it doesn't have ->
//! double fault) would try to run the double-fault handler on the
//! same exhausted stack, causing a triple fault and an instant reset
//! with zero diagnostic output. This is a well-documented x86_64 OS
//! dev pitfall, not a hypothetical.

use x86_64::VirtAddr;
use x86_64::structures::gdt::{Descriptor, GlobalDescriptorTable, SegmentSelector};
use x86_64::structures::tss::TaskStateSegment;

pub const DOUBLE_FAULT_IST_INDEX: u16 = 0;

struct Selectors {
    code_selector: SegmentSelector,
    tss_selector: SegmentSelector,
    user_code_selector: SegmentSelector,
    user_data_selector: SegmentSelector,
}

static mut TSS: TaskStateSegment = TaskStateSegment::new();
static mut GDT: Option<(GlobalDescriptorTable, Selectors)> = None;

/// 20KiB, page-aligned-enough for our purposes, never freed -- this is
/// a static emergency stack, not something the allocator (which
/// doesn't exist yet at this boot phase) manages.
const DOUBLE_FAULT_STACK_SIZE: usize = 4096 * 5;
static mut DOUBLE_FAULT_STACK: [u8; DOUBLE_FAULT_STACK_SIZE] = [0; DOUBLE_FAULT_STACK_SIZE];

/// The ring-0 stack the CPU automatically switches to via the TSS
/// whenever a ring-3->ring-0 privilege change happens on interrupt/trap
/// entry (required for `crate::experimental::usermode` -- without a
/// valid RSP0, entering ring 3 at all would make the *first* interrupt
/// back into the kernel (including the syscall trap itself) load an
/// invalid stack pointer from an uninitialized TSS field).
const PRIVILEGE_STACK_SIZE: usize = 4096 * 5;
static mut PRIVILEGE_STACK: [u8; PRIVILEGE_STACK_SIZE] = [0; PRIVILEGE_STACK_SIZE];

/// MUST be called exactly once, during [`crate::boot_phase::BootPhase::GdtInit`],
/// after [`crate::boot_phase::BootPhase::LongModeEntry`] and before
/// [`crate::boot_phase::BootPhase::IdtInit`] (the IDT's double-fault
/// entry references [`DOUBLE_FAULT_IST_INDEX`], so the TSS/GDT that
/// index refers to must already be loaded).
pub fn init() {
    unsafe {
        let stack_start = VirtAddr::from_ptr(core::ptr::addr_of!(DOUBLE_FAULT_STACK));
        let stack_end = stack_start + DOUBLE_FAULT_STACK_SIZE as u64;
        TSS.interrupt_stack_table[DOUBLE_FAULT_IST_INDEX as usize] = stack_end;

        let priv_stack_start = VirtAddr::from_ptr(core::ptr::addr_of!(PRIVILEGE_STACK));
        let priv_stack_end = priv_stack_start + PRIVILEGE_STACK_SIZE as u64;
        TSS.privilege_stack_table[0] = priv_stack_end;

        let mut gdt = GlobalDescriptorTable::new();
        let code_selector = gdt.append(Descriptor::kernel_code_segment());
        // x86_64's Descriptor::user_data_segment()/user_code_segment()
        // return DPL=3 descriptors. GDT ordering matters on real x86_64
        // hardware for the SYSRET-style "STAR MSR" convention, but since
        // this kernel enters ring 3 via IRETQ (not SYSCALL/SYSRET), the
        // specific slot order doesn't matter here -- only that both
        // exist and are readable segment selectors with RPL 3.
        let user_data_selector = gdt.append(Descriptor::user_data_segment());
        let user_code_selector = gdt.append(Descriptor::user_code_segment());
        let tss_ptr = core::ptr::addr_of!(TSS);
        let tss_selector = gdt.append(Descriptor::tss_segment(&*tss_ptr));
        GDT = Some((
            gdt,
            Selectors {
                code_selector,
                tss_selector,
                user_code_selector,
                user_data_selector,
            },
        ));

        let (gdt_ref, selectors) = (*&raw const GDT).as_ref().unwrap();
        gdt_ref.load();

        use x86_64::instructions::segmentation::{Segment, CS};
        use x86_64::instructions::tables::load_tss;
        CS::set_reg(selectors.code_selector);
        load_tss(selectors.tss_selector);
    }
}

/// Read back after [`init`] by `crate::experimental::usermode` --
/// separated from `init` itself so nothing outside this module ever
/// needs `unsafe` to read a selector value.
pub fn user_code_selector() -> SegmentSelector {
    unsafe { (*&raw const GDT).as_ref().unwrap().1.user_code_selector }
}

pub fn user_data_selector() -> SegmentSelector {
    unsafe { (*&raw const GDT).as_ref().unwrap().1.user_data_selector }
}

/// Updates the ring-0 stack the CPU will switch to on the *next*
/// ring-3->ring-0 privilege change. Used by
/// `crate::experimental::address_space::AddressSpace::activate` so
/// every process gets its own private kernel-side trap-entry stack
/// instead of every ring-3 process sharing the one static
/// [`PRIVILEGE_STACK`] `init` sets up at boot -- correctness matters
/// here specifically once a process's trapped state needs to be
/// *resumed* later (not just reported-and-abandoned, which is all
/// this kernel's demos have needed so far): two processes sharing one
/// RSP0 would have the second process's trap entry overwrite the
/// first's still-suspended saved state on the same physical stack
/// memory. Fixed proactively here even though no current demo yet
/// exercises resume-after-suspend for a ring-3 process, rather than
/// waiting for that to become a real, harder-to-diagnose bug later.
///
/// # Safety
/// `top` must be the top (highest address) of a valid, exclusively-
/// owned stack region that will remain allocated for as long as the
/// associated process might trap.
pub unsafe fn set_rsp0(top: VirtAddr) {
    TSS.privilege_stack_table[0] = top;
}
