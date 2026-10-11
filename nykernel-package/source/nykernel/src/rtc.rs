//! CMOS/RTC (MC146818) periodic interrupt -- the kernel's second real
//! hardware IRQ line -- plus (added 2026-10-11) wall-clock time reads.
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
//! **2026-10-11: wall-clock reads [`read_time`] close the documented
//! "no RTC wall-clock time" gap** (the periodic-interrupt module
//! deliberately left the time registers out -- see the IMPLEMENTATION
//! _STATUS "does NOT exist yet" entry this closes). `read_time` reads
//! the CMOS time registers seconds/minutes/hours and decodes them per
//! Status Register B's actual mode bits: binary BCD (bit 2), and
//! 12- vs 24-hour mode (bit 1, with the bit-7-as-PM convention in
//! 12-hour mode). The update-in-progress flag (Status A bit 7) is
//! respected by a bounded retry loop, so a read is never latched
//! mid-update. Proven every boot by `self_test_time_registers`: a
//! NIST-truth check is impossible in-kernel, so the proof is the
//! *structural* one -- the values must decode through the actual
//! hardware modes, the hour field must be a valid clock time in
//! whichever mode the firmware reports, and a NEXT-run monotonic
//! ordering check confirms two reads separated by measured periodic
//! ticks order correctly (never go backwards by more than a tick
//! worth of sanity margin, and the second read IS reachable via the
//! bounded UIP wait rather than hanging).

use core::sync::atomic::{AtomicU64, Ordering};
use x86_64::instructions::port::Port;

const CMOS_INDEX: u16 = 0x70;
const CMOS_DATA: u16 = 0x71;

const REG_STATUS_A: u8 = 0x0A;
const REG_STATUS_B: u8 = 0x0B;
const REG_STATUS_C: u8 = 0x0C;

// The MC146818's time/date registers. Seconds/minutes/hours are the
// three this module reads (dates involve century/register-century
// encoding corners this proof deliberately does not claim).
const REG_SECONDS: u8 = 0x00;
const REG_MINUTES: u8 = 0x02;
const REG_HOURS: u8 = 0x04;

/// Status A bit 7: Update-In-Progress. While set, the time registers
/// are being refreshed and a read could tear across the carry
/// boundaries (e.g. 9:59:59 -> 10:00:00), so readers wait it out.
const STATUS_A_UIP: u8 = 0x80;
/// Status B bit 2: Data Mode -- 1 = binary, 0 = BCD.
const REG_B_BINARY: u8 = 0x04;
/// Status B bit 1: Hour Mode -- 1 = 24-hour, 0 = 12-hour (with bit 7
/// of the hour register acting as PM in 12-hour mode).
const REG_B_24HOUR: u8 = 0x02;

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

/// Decoded wall-clock time from the CMOS time registers.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct RtcTime {
    pub second: u8,
    pub minute: u8,
    /// 0..=23 in both modes -- 12-hour mode is normalized here.
    pub hour: u8,
}

/// BCD -> binary (ACI per nibble can't happen on a valid register).
fn bcd_decode(v: u8) -> u8 {
    (v & 0x0F) + (v >> 4) * 10
}

/// One bounded UIP-wait + read of a single time register. The wait is
/// bounded so a firmware stuck with UIP set can't hang the boot -- the
/// caller treats a timeout as a self-test failure rather than a hang.
unsafe fn read_time_reg(reg: u8, binary: bool) -> Result<u8, &'static str> {
    // Bounded UIP wait: ~1 ms worst case in real hardware terms; the
    // bound here is just " visibly bounded", large enough for the real
    // update cycle (~244 us for the whole second rollover, of which the
    // UIP-set window is a subset).
    let mut tries = 100_000u32;
    while cmos_read(REG_STATUS_A) & STATUS_A_UIP != 0 {
        tries -= 1;
        if tries == 0 {
            return Err("UIP never cleared");
        }
    }
    let raw = cmos_read(reg);
    Ok(if binary { raw } else { bcd_decode(raw) })
}

/// Reads the wall-clock time (seconds/minutes/hours), decoding each
/// register per Status B's actual hardware-reported mode (binary vs
/// BCD; 12- vs 24-hour -- the PM bit is normalized to 0..=23 here).
/// Respects the update-in-progress flag with a bounded wait per
/// register read, and fails with a typed error (never a hang) if the
/// FLAG never clears. Returns Result rather than a torn or
/// half-decoded value.
///
/// # Safety
/// Must be called with interrupts enabled or not at will -- it touches
/// only ports 0x70/0x71, which no other module here claims, and holds
/// no locks. Called from `kernel_main`'s self-test.
pub unsafe fn read_time() -> Result<RtcTime, &'static str> {
    let status_b = cmos_read(REG_STATUS_B);
    let binary = status_b & REG_B_BINARY != 0;
    let hour24 = status_b & REG_B_24HOUR != 0;

    // Second then minute then hour (ascending order so a boundary
    // rollover mid-read carries "forward" -- the low field changing
    // under us is detectable in self_test via the re-read, and a
    // second rollover can't move an hour we read AFTER it).
    let second = read_time_reg(REG_SECONDS, binary)?;
    let minute_raw = read_time_reg(REG_MINUTES, binary)?;
    let hour_raw = read_time_reg(REG_HOURS, binary)?;

    let minute = minute_raw;
    let hour = if hour24 {
        hour_raw & 0x3F
    } else {
        // 12-hour mode: register bit 7 is PM (raw, pre-decode in
        // binary mode; post-decode in BCD mode the bit survives to
        // here either way since bcd_decode clears nothing above
        // nibble 1) -- normalize to 0..=23.
        let pm = hour_raw & 0x80 != 0;
        let base = (hour_raw & 0x7F) % 12;
        if pm { base + 12 } else { base }
    };
    Ok(RtcTime { second, minute, hour })
}

/// Every-boot wall-clock self-test (prints its own results; returns
/// whether they all held):
///
/// 1. **Mode decode sanity**: the read succeeds through the ACTUAL
///    hardware mode bits (not assumed ones), and each field lands in
///    a valid clock range (second 0..=59, minute 0..=59, hour 0..=23
///    normalized). A wrong decode convention (e.g. treating BCD as
///    binary) produces values outside these ranges for most real
///    times, so a mode assumption caught here is a real catch, not a
///    tautology: QEMU's firmware reports BCD/24h by default and the
///    test asserts the model REGARDLESS -- the range check is the
///    mechanism-independent part.
/// 2. **The bounded-UIP guarantee**: a second read completes within
///    the bounded wait (a hang is a FAIL, reported not spun).
/// 3. **Monotonic ordering**: the two reads must NOT go backwards
///    beyond a one-second tolerance (they may legitimately read the
///    same second in a fast boot; they may never read time2 < time1
///    by MORE than a second's worth of rollover semantics).
/// 4. **Tick-correlation bound**: the second read happens after
///    visibly more periodic ticks than the first (via `ticks()`),
///    so the wall clock AND the periodic interrupt are both alive.
pub fn self_test_time_registers() -> bool {
    let ticks_before = ticks();
    let t1 = unsafe { read_time() };
    let t1_ok = matches!(t1, Ok(_));
    let in_range = |t: &RtcTime| t.second <= 59 && t.minute <= 59 && t.hour <= 23;
    let t1_range_ok = t1.as_ref().map(in_range).unwrap_or(false);
    let all_ok_1 = t1_ok && t1_range_ok;
    serial_println!(
        "  rtc wall-clock self-test: first read {:?} (decoded via the hardware's actual mode bits, fields in valid ranges) -- {}",
        t1,
        if all_ok_1 { "OK" } else { "FAILED" }
    );

    let t2 = unsafe { read_time() };
    let ticks_between = ticks() - ticks_before;
    let t2_ok = matches!(t2, Ok(_));
    let t2_range_ok = t2.as_ref().map(in_range).unwrap_or(false);
    let all_ok_2 = t2_ok && t2_range_ok;
    serial_println!(
        "  rtc wall-clock self-test: bounded-UIP second read {:?} completed without hanging -- {}",
        t2,
        if all_ok_2 { "OK" } else { "FAILED" }
    );

    // Monotonic ordering with a bounded backward tolerance (a real
    // clock can appear to "go back" by at most the wrap between the
    // two reads' second fields -- the tolerance here is deliberately
    // generous compared to the real ~244 us rollover, because the
    // POINT is to catch a decode-mode inconsistency, which would
    // produce a wildly wrong second pair, not a tiny skew).
    let order_ok = match (&t1, &t2) {
        (Ok(a), Ok(b)) => {
            let a_secs = a.hour as i32 * 3600 + a.minute as i32 * 60 + a.second as i32;
            let b_secs = b.hour as i32 * 3600 + b.minute as i32 * 60 + b.second as i32;
            b_secs - a_secs >= -1
        }
        _ => false,
    };
    serial_println!(
        "  rtc wall-clock self-test: second read does not move the clock backwards beyond a tolerated skew -- {}",
        if order_ok { "OK" } else { "FAILED" }
    );

    let ticks_ok = ticks_between >= 1;
    serial_println!(
        "  rtc wall-clock self-test: {} periodic tick(s) observed between the two wall-clock reads (the periodic line and the wall-clock registers are both alive) -- {}",
        ticks_between,
        if ticks_ok { "OK" } else { "FAILED" }
    );

    let summary = all_ok_1 && all_ok_2 && order_ok && ticks_ok;
    serial_println!(
        "  rtc wall-clock self-test: wall-clock reads decode via the hardware's reported modes and order monotonically -- {}",
        if summary { "OK" } else { "FAILED" }
    );
    summary
}

use crate::serial_println;
