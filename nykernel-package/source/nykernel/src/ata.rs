//! ATA/ATAPI (PIIX-style IDE) channel interrupts -- IRQ14 (primary)
//! and IRQ15 (secondary), with real handlers.
//!
//! Closes the "ATA/IDE" half of the "more IRQ lines with real handlers"
//! gap named in IMPLEMENTATION_STATUS.md. Where `keyboard.rs` injects
//! its own scancode to prove IRQ1 input-free, this module proves the
//! IDE lines the way a tiny driver actually would: it selects the
//! primary/secondary master and issues a real `IDENTIFY (PACKET)
//! DEVICE` command, then waits for the channel interrupt the device
//! raises on completion, reporting the device's completion status.
//!
//! # Honest scope (this is the genuinely device-dependent one)
//!
//! Unlike the periodic RTC tick or the injected keyboard scancode, an
//! IDE interrupt only happens if a device is actually attached and
//! responds. So this module is explicitly **conditional**: it probes
//! for a device first, and if none is present it reports that plainly
//! and does nothing else. When a device *is* present (QEMU's default
//! `pc` machine with the boot ISO attached as a CD-ROM does have one,
//! on the secondary channel) the self-test is a real, bounded proof
//! that the IRQ line and handler work. It deliberately does NOT read
//! sectors, cache data, or implement any filesystem -- only enough
//! register access to make the device raise its completion interrupt
//! and to read the identification block it returns.
//!
//! The IDENTIFY command byte is chosen adaptively: `0xA1` (IDENTIFY
//! PACKET DEVICE) if the drive shows the ATAPI signature, else `0xEC`
//! (IDENTIFY DEVICE); if the first attempt completes with the error bit
//! set and no data, the other is retried. Which command actually
//! succeeded is reported, so the transcript never overstates the case.

use core::sync::atomic::{AtomicU64, Ordering};
use x86_64::instructions::port::Port;

/// IRQ line of the primary IDE channel.
pub const IRQ_ATA_PRIMARY: u8 = 14;
/// IRQ line of the secondary IDE channel.
pub const IRQ_ATA_SECONDARY: u8 = 15;

struct Channel {
    base: u16,
    ctrl: u16,
}

const CHANNELS: [Channel; 2] = [
    Channel { base: 0x1F0, ctrl: 0x3F6 }, // primary
    Channel { base: 0x170, ctrl: 0x376 }, // secondary
];

// Register offsets from `base`.
const REG_DATA: u16 = 0;
const REG_SECCOUNT: u16 = 2;
const REG_LBA0: u16 = 3;
const REG_LBA1: u16 = 4;
const REG_LBA2: u16 = 5;
const REG_DRIVEHEAD: u16 = 6;
const REG_STATUS: u16 = 7;

const STATUS_ERR: u8 = 0x01;
const STATUS_DRQ: u8 = 0x08;

/// Bounded wait for a completion interrupt, so a device that never
/// answers can't hang the boot forever -- a timeout is a reportable
/// failure, not an infinite silent spin.
const MAX_IRQ_SPINS: u32 = 50_000_000;

/// Per-channel interrupt counters, indexed the same way as `CHANNELS`.
static IRQS: [AtomicU64; 2] = [AtomicU64::new(0), AtomicU64::new(0)];
/// Status byte observed at each channel's most recent interrupt.
static LAST_STATUS: [AtomicU64; 2] = [AtomicU64::new(0), AtomicU64::new(0)];

pub fn irqs(channel: usize) -> u64 {
    IRQS[channel].load(Ordering::Relaxed)
}

pub fn last_status(channel: usize) -> u8 {
    LAST_STATUS[channel].load(Ordering::Relaxed) as u8
}

/// The IRQ line for a channel index returned by [`detect`].
pub fn irq_for(channel: usize) -> u8 {
    if channel == 0 {
        IRQ_ATA_PRIMARY
    } else {
        IRQ_ATA_SECONDARY
    }
}

unsafe fn read_reg(base: u16, off: u16) -> u8 {
    Port::<u8>::new(base + off).read()
}

unsafe fn write_reg(base: u16, off: u16, value: u8) {
    Port::<u8>::new(base + off).write(value);
}

unsafe fn read_alt(ctrl: u16) -> u8 {
    Port::<u8>::new(ctrl).read()
}

/// Standard 400 ns delay after a drive select: four reads of the
/// alternate status register (the device-control port), a no-op the
/// spec sanctions for exactly this.
unsafe fn delay_400ns(ctrl: u16) {
    for _ in 0..4 {
        let _ = read_alt(ctrl);
    }
}

/// Probes both channels for a master device. Returns the index of the
/// first channel with one, or `None` if neither has a device (a
/// machine with no IDE attached) -- in which case there is genuinely
/// nothing to prove and the caller must not pretend otherwise.
///
/// A channel with no device reads back `0x00` (QEMU's floating bus) or
/// `0xFF` from its status port after a select; a present device clears
/// BSY and shows a real signature.
pub fn detect() -> Option<usize> {
    for ch in 0..CHANNELS.len() {
        let c = &CHANNELS[ch];
        unsafe {
            write_reg(c.base, REG_DRIVEHEAD, 0xA0); // select master, LBA mode
            delay_400ns(c.ctrl);
            let status = read_reg(c.base, REG_STATUS);
            if status != 0x00 && status != 0xFF {
                return Some(ch);
            }
        }
    }
    None
}

/// Clears `nIEN` in the channel's device-control register so the device
/// is allowed to assert its interrupt line. Safe to call on a channel
/// with no device.
pub unsafe fn enable_interrupts(channel: usize) {
    let c = &CHANNELS[channel];
    // The device-control register lives at `ctrl` (0x3F6/0x376), not at
    // `base+7` (that is the command/status register). Bit 1 is `nIEN`;
    // clearing it lets the device assert its interrupt line.
    Port::<u8>::new(c.ctrl).write(0);
}

unsafe fn issue_command(channel: usize, command: u8, atapi: bool) {
    let c = &CHANNELS[channel];
    write_reg(c.base, REG_DRIVEHEAD, 0xA0);
    delay_400ns(c.ctrl);
    // Zero the count/LBA registers. (For ATAPI, LBA1/LBA2 are the byte
    // count limit when non-zero, which is why they're cleared here.)
    let _ = atapi;
    write_reg(c.base, REG_SECCOUNT, 0);
    write_reg(c.base, REG_LBA0, 0);
    write_reg(c.base, REG_LBA1, 0);
    write_reg(c.base, REG_LBA2, 0);
    write_reg(c.base, REG_STATUS, command);
}

/// Issues an IDENTIFY command on `channel`, waiting (bounded) for the
/// completion interrupt. Returns `(command, completion_status,
/// interrupts_seen, ok)`, where `ok` is true when the device reported
/// data ready without an error.
///
/// Tries the command the drive's signature suggests first, and the
/// other on an aborted first attempt -- an ATAPI drive that has left
/// its power-on signature state still completes `IDENTIFY PACKET
/// DEVICE` (`0xA1`), and an ATA drive completes `IDENTIFY DEVICE`
/// (`0xEC`).
///
/// # Safety
/// The channel must have been confirmed present by [`detect`], its IRQ
/// line unmasked with a handler installed, and [`enable_interrupts`]
/// called.
pub unsafe fn identify(channel: usize) -> (u8, u8, u64, bool) {
    let c = &CHANNELS[channel];

    write_reg(c.base, REG_DRIVEHEAD, 0xA0);
    delay_400ns(c.ctrl);
    let seccount = read_reg(c.base, REG_SECCOUNT);
    let lba1 = read_reg(c.base, REG_LBA1);
    let lba2 = read_reg(c.base, REG_LBA2);
    let atapi = seccount == 0x01 && lba1 == 0x14 && lba2 == 0xEB;

    let order: [u8; 2] = if atapi { [0xA1, 0xEC] } else { [0xEC, 0xA1] };
    let mut total = 0u64;
    let mut last_command = order[0];
    let mut status_seen = 0u8;

    for &command in order.iter() {
        let before = irqs(channel);
        issue_command(channel, command, command == 0xA1);

        let mut spins = 0u32;
        while irqs(channel) == before && spins < MAX_IRQ_SPINS {
            core::hint::spin_loop();
            spins += 1;
        }
        let seen = irqs(channel) - before;
        total += seen;

        last_command = command;
        status_seen = last_status(channel);
        if seen >= 1 && status_seen & STATUS_ERR == 0 && status_seen & STATUS_DRQ != 0 {
            return (command, status_seen, total, true);
        }
    }

    (last_command, status_seen, total, false)
}

/// Called from the channel's IRQ handler: read the status register
/// (which acknowledges/deasserts the device's interrupt line), record
/// it, drain the IDENTIFY result if data is ready, and count the
/// interrupt.
pub fn handle(channel: usize) {
    let c = &CHANNELS[channel];
    unsafe {
        let status = read_reg(c.base, REG_STATUS);
        LAST_STATUS[channel].store(status as u64, Ordering::Relaxed);
        if status & STATUS_DRQ != 0 {
            // 256 words of IDENTIFY data: read and discard, so the
            // device returns to a clean idle state.
            let mut data: Port<u16> = Port::new(c.base + REG_DATA);
            for _ in 0..256 {
                let _ = data.read();
            }
        }
    }
    IRQS[channel].fetch_add(1, Ordering::Relaxed);
}
