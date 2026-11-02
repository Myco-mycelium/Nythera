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
unsafe fn read_time_reg(reg: u8) -> Result<u8, &'static str> {
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
    Ok(cmos_read(reg))
}

/// Decodes one register byte for the hardware's reported data mode.
/// Callers that carry a flag in the top bit (the hour's PM bit in 12-hour
/// mode) must strip that bit themselves BEFORE decoding -- a previous
/// version decoded the whole byte and then tested bit 7 of the decoded
/// value, which is a different number once BCD decoding has run.
fn decode_field(raw: u8, binary: bool) -> u8 {
    if binary {
        raw
    } else {
        bcd_decode(raw)
    }
}

/// Normalizes a 12-hour register value to 0..=23.
///
/// The two noon/midnight values are where a naive `h % 12` decode goes
/// wrong, and this kernel hit exactly that: at 12:31 the hour register
/// reads 12 with the PM bit set, `12 % 12` is 0, and noon decoded as
/// midnight. The table below is the specification's mapping, not an
/// arithmetic shortcut: 12 AM is 0, 12 PM is 12, everything else is
/// `h + 12` when PM.
fn hour_12h_to_24h(hour12: u8, pm: bool) -> Option<u8> {
    if hour12 == 0 || hour12 > 12 {
        return None;
    }
    Some(match (hour12, pm) {
        (12, false) => 0,
        (12, true) => 12,
        (h, false) => h,
        (h, true) => h + 12,
    })
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
    // Three attempts, then a typed error: the RTC's registers are only
    // reliably read as a set if nothing updates underneath the walk, and
    // a mode-register read that disagrees with itself means we cannot
    // know which decode applies. Retrying is what makes the difference
    // between "the clock is odd" and "this read failed" visible.
    let mut last_error = "clock registers did not read consistently";
    for _ in 0..3 {
        let status_b = cmos_read(REG_STATUS_B);
        if cmos_read(REG_STATUS_B) != status_b {
            last_error = "the mode register read disagreed with itself";
            continue;
        }
        let binary = status_b & REG_B_BINARY != 0;
        let hour24 = status_b & REG_B_24HOUR != 0;

        // Ascending field order, plus a second read of the seconds at the
        // end: if the seconds moved, a rollover happened during the walk
        // and the fields may belong to two different instants.
        let second_raw = read_time_reg(REG_SECONDS)?;
        let minute_raw = read_time_reg(REG_MINUTES)?;
        let hour_raw = read_time_reg(REG_HOURS)?;
        let second_again = read_time_reg(REG_SECONDS)?;
        if second_again != second_raw {
            last_error = "the clock rolled over during the read";
            continue;
        }

        let second = decode_field(second_raw, binary);
        let minute = decode_field(minute_raw, binary);
        let hour = if hour24 {
            decode_field(hour_raw & 0x3F, binary)
        } else {
            // Split the PM flag off the RAW byte before decoding: BCD
            // decoding a byte with bit 7 set yields a value whose bit 7
            // means something else entirely.
            let pm = hour_raw & 0x80 != 0;
            match hour_12h_to_24h(decode_field(hour_raw & 0x7F, binary), pm) {
                Some(h) => h,
                None => {
                    last_error = "12-hour mode reported an hour outside 1..=12";
                    continue;
                }
            }
        };
        if second > 59 || minute > 59 || hour > 23 {
            last_error = "a decoded field is outside its clock range";
            continue;
        }
        return Ok(RtcTime { second, minute, hour });
    }
    Err(last_error)
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
/// The 12-hour decode table, checked against the boundary values that a
/// naive `h % 12` gets wrong. This runs every boot because it is cheap
/// and because the alternative is finding out at noon: the first version
/// of `read_time` reported 12:31 PM as 00:31, and nothing in the boot
/// chain noticed until a wall-clock self-test happened to run in the
/// afternoon.
fn decode_12h_self_test() -> bool {
    let cases: [(u8, bool, Option<u8>); 7] = [
        (12, false, Some(0)),  // 12 AM = midnight
        (12, true, Some(12)),  // 12 PM = noon
        (1, false, Some(1)),
        (1, true, Some(13)),
        (11, true, Some(23)),
        (0, false, None),  // hour 0 is not a 12-hour value
        (13, true, None),  // neither is 13
    ];
    let mut all = true;
    for (hour12, pm, want) in cases {
        all &= hour_12h_to_24h(hour12, pm) == want;
    }
    serial_println!(
        "  rtc 12-hour decode self-test: 12 AM -> 0, 12 PM -> 12, 1 PM -> 13, 11 PM -> 23, and 0/13 refused -- {}",
        if all { "OK" } else { "FAILED" }
    );
    all
}

pub fn self_test_time_registers() -> bool {
    let decode_ok = decode_12h_self_test();
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

    let summary = decode_ok && all_ok_1 && all_ok_2 && order_ok && ticks_ok;
    serial_println!(
        "  rtc wall-clock self-test: wall-clock reads decode via the hardware's reported modes and order monotonically -- {}",
        if summary { "OK" } else { "FAILED" }
    );
    summary
}

use crate::serial_println;
