//! CMOS/RTC (MC146818) periodic interrupt -- the kernel's second real
//! hardware IRQ line.
//!
//! Closes the "Interrupt handling beyond the timer (IRQ0)" gap named in
//! IMPLEMENTATION_STATUS.md. Until now exactly one IRQ line was
//! unmasked (the PIT on IRQ0), and the module docs for `pic.rs` were
//! explicit about why: "unmasking an IRQ with no handler wired up is
//! worse than not supporting it at all." This module is a second line
//! that *does* have a real handler, so it can be unmasked honestly.
//!
//! Why the RTC for this particular line: it is the simplest IRQ that
//! needs no input at all to test -- its *periodic* interrupt fires on
//! its own at a programmable rate, so a self-test observes real
//! interrupts exactly the way the PIT self-test does. (The keyboard
//! line, which looks untestable because it is input-driven, is added
//! later anyway and proven input-free by having the guest inject its
//! own scancode through the 8042 controller -- see `crate::keyboard`.)
//!
//! IRQ8 lives on the slave 8259 (IRQ8-15, vectors 0x28-0x2F after
//! `pic::init`'s remap), so this also exercises the cascade path that
//! the PIT never did: the slave's line reaches the CPU only through
//! IRQ2 on the master, both PICs must be unmasked, and the handler
//! must EOI *both*. Getting that wrong is a classic hang-with-no-EIO
//! bug, which is exactly why it is worth proving rather than assuming.
//!
//! Does NOT read the wall-clock time/date -- this is purely the
//! periodic tick, deliberately narrow. Reading CMOS time registers
//! (with their BCD/binary and 12/24-hour modes) is separate future
//! work.

use core::sync::atomic::{AtomicU64, Ordering};
use x86_64::instructions::port::Port;

const CMOS_INDEX: u16 = 0x70;
const CMOS_DATA: u16 = 0x71;

const REG_STATUS_A: u8 = 0x0A;
const REG_STATUS_B: u8 = 0x0B;
const REG_STATUS_C: u8 = 0x0C;

/// IRQ line this module owns. 8 = RTC on the slave 8259.
pub const IRQ_RTC: u8 = 8;
/// The master PIC line the slave arrives on -- unmasking IRQ8 without
/// also unmasking IRQ2 would leave the slave's signal unable to reach
/// the CPU at all.
pub const IRQ_CASCADE: u8 = 2;

/// Periodic-interrupt rate select (register A, low nibble). `0b0110`
/// is the MC146818's 1024 Hz setting -- fast enough that a handful of
/// ticks arrive in a few milliseconds, slow enough not to starve the
/// rest of the boot.
const RATE_SELECT_1024HZ: u8 = 0b0110;
/// Register B bit 6: Periodic Interrupt Enable.
const REG_B_PIE: u8 = 0x40;

/// Incremented once per RTC periodic interrupt. Same ordering rationale
/// as `idt::TIMER_TICKS`: a coarse monotonic counter, not a
/// synchronization primitive for other memory.
static RTC_TICKS: AtomicU64 = AtomicU64::new(0);

pub fn ticks() -> u64 {
    RTC_TICKS.load(Ordering::Relaxed)
}

unsafe fn cmos_read(reg: u8) -> u8 {
    let mut index: Port<u8> = Port::new(CMOS_INDEX);
    let mut data: Port<u8> = Port::new(CMOS_DATA);
    index.write(reg);
    data.read()
}

unsafe fn cmos_write(reg: u8, value: u8) {
    let mut index: Port<u8> = Port::new(CMOS_INDEX);
    let mut data: Port<u8> = Port::new(CMOS_DATA);
    index.write(reg);
    data.write(value);
}

/// Programs the RTC for a 1024 Hz periodic interrupt and enables it.
/// Does not touch the PIC mask -- see [`crate::pic`] and `kernel_main`:
/// the RTC line is unmasked alongside the PIT's, after this has wired
/// the periodic interrupt up, so there is no window where a tick could
/// arrive with no handler.
///
/// # Safety
/// Must be called after `idt::init` (the vector-0x28 handler must be
/// installed) and at most once.
pub unsafe fn init() {
    // Read register C once: it latches/clears any pending interrupt
    // flag, so a stale one can't fire immediately after we enable.
    let _ = cmos_read(REG_STATUS_C);

    // Set the periodic rate without disturbing the upper divider bits.
    let status_a = cmos_read(REG_STATUS_A);
    cmos_write(REG_STATUS_A, (status_a & 0xF0) | RATE_SELECT_1024HZ);

    // Enable periodic interrupts, preserving every other control bit
    // (24-hour mode, binary vs BCD, daylight-saving, ...) -- this
    // module only owns the PIE bit.
    let status_b = cmos_read(REG_STATUS_B);
    cmos_write(REG_STATUS_B, status_b | REG_B_PIE);
}

/// Called from the vector-0x28 handler on every RTC interrupt:
/// acknowledge it at the source (the MC146818 keeps its IRQ line
/// asserted until register C is read) and count it.
pub fn handle() {
    unsafe {
        let _ = cmos_read(REG_STATUS_C);
    }
    RTC_TICKS.fetch_add(1, Ordering::Relaxed);
}
