//! Boot-phase state machine.
//!
//! Implements NPS-001 §5's phase-transition order-validation
//! requirement (added via the Milestone-10 threat-model amendment
//! described in `docs/reference/security/NPS-023-secure-boot-threat-model.md`:
//! "boot-phase transitions aren't order-validated at the API level" was
//! the finding; the fix was to require exactly this). Every later boot
//! step (GDT, IDT, memory init, ...) must advance through this state
//! machine via [`advance`], which panics -- a boot-time panic halts via
//! [`crate::hlt_loop`], which is the only acceptable failure mode this
//! early -- rather than silently letting a phase run out of order or
//! run twice.
//!
//! This is intentionally a strict linear order for Phase 1. A real
//! kernel's boot sequence has some legitimately parallel or
//! conditional phases (e.g. per-CPU AP bring-up); this module's job
//! right now is only to make the *sequential* phases NPS-001 already
//! describes impossible to reorder or skip by construction, not to
//! model the full eventual boot graph.

use core::sync::atomic::{AtomicU8, Ordering};

#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BootPhase {
    /// CPU handed off from the Multiboot2 loader in 32-bit protected
    /// mode; boot.s has already validated the multiboot magic, CPUID
    /// availability, and long-mode support (the checks that MUST
    /// happen before anything else, since nothing later is even
    /// executable without long mode).
    LoaderHandoff = 0,
    /// Identity paging installed, long mode entered, 64-bit GDT
    /// loaded, control transferred to `kernel_main`. This is the
    /// first phase Rust code observes directly.
    LongModeEntry = 1,
    /// A proper kernel-owned GDT (not boot.s's disposable bootstrap
    /// one) plus TSS with a dedicated double-fault IST stack is live.
    GdtInit = 2,
    /// IDT installed; CPU exceptions (divide error, page fault,
    /// general protection fault, double fault, ...) are handled
    /// instead of triple-faulting the machine.
    IdtInit = 3,
    /// Early diagnostics (serial UART) are up; from this point on,
    /// boot progress and failures are reported via serial, not just
    /// the VGA-buffer error codes boot.s used before Rust existed.
    DiagnosticsInit = 4,
    /// The Multiboot2 memory map has been parsed, a physical frame
    /// allocator is live, dynamic page-table mapping is available
    /// (not just Phase 1's static identity-mapped first 1GiB), and a
    /// kernel heap is mapped and handed to `#[global_allocator]` --
    /// `alloc::*` (Vec, Box, ...) is usable from this point on.
    MemoryInit = 5,
    /// The legacy 8259 PICs are remapped off the CPU exception vector
    /// range (see `src/pic.rs` for why that was unsafe before this
    /// phase) and the timer IRQ (IRQ0) is unmasked with a real handler
    /// installed. This is the first point at which `sti` is safe to
    /// execute, and the first point at which the kernel is genuinely
    /// interrupt-driven rather than purely polling/sequential.
    InterruptControllerInit = 6,
    /// Phase 1+2+3-prep bring-up's terminal state: the kernel is
    /// alive, handling exceptions, managing its own memory, and
    /// servicing real hardware timer interrupts while idling. Actual
    /// scheduling (multiple execution contexts, blocked on NPS-002
    /// leaving Draft) and kernel IPC (Phase 4, blocked on NPS-003
    /// leaving Draft) are not yet reachable states -- see
    /// IMPLEMENTATION_STATUS.md.
    Ready = 7,
}

static CURRENT_PHASE: AtomicU8 = AtomicU8::new(BootPhase::LoaderHandoff as u8);

/// Advance to `next`, which MUST be exactly the phase immediately
/// after the current one. Panics (halting via the boot-time panic
/// handler) on any attempt to skip, repeat, or go backward -- per
/// NPS-001 §5, an out-of-order transition is a boot-time fault, not a
/// warning.
pub fn advance(next: BootPhase) {
    let current = CURRENT_PHASE.load(Ordering::SeqCst);
    let expected_next = current + 1;
    if next as u8 != expected_next {
        panic!(
            "boot phase order violation: at phase {}, attempted transition to {} (only {} is valid) -- NPS-001 section 5",
            current, next as u8, expected_next
        );
    }
    CURRENT_PHASE.store(next as u8, Ordering::SeqCst);
}

pub fn current() -> u8 {
    CURRENT_PHASE.load(Ordering::SeqCst)
}
