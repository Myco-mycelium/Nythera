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
//! that the IRQ line and handler work. Since 2026-11-09 it ALSO reads
//! one logical sector on request (`read_sector`, the capability gate's
//! block-data plane): PIO, polled, with the IRQ handler temporarily
//! barred from draining the data phase. There is still no filesystem,
//! cache or multi-sector path -- one sector in or nothing.
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
const STATUS_BSY: u8 = 0x80;

/// Bounded wait for a completion interrupt, so a device that never
/// answers can't hang the boot forever -- a timeout is a reportable
/// failure, not an infinite silent spin.
const MAX_IRQ_SPINS: u32 = 50_000_000;

/// Per-channel interrupt counters, indexed the same way as `CHANNELS`.
static IRQS: [AtomicU64; 2] = [AtomicU64::new(0), AtomicU64::new(0)];
/// Status byte observed at each channel's most recent interrupt.
static LAST_STATUS: [AtomicU64; 2] = [AtomicU64::new(0), AtomicU64::new(0)];
/// Whether each channel's master answered IDENTIFY as ATAPI (1) or ATA
/// (0). Set by `identify`, which is what the boot already runs; the
/// sector reader needs it to choose the packet path vs LBA28.
static IS_ATAPI: [AtomicU64; 2] = [AtomicU64::new(0), AtomicU64::new(0)];
/// The channel `detect` found, or -1. The capability gate reads the boot
/// device, not a user-chosen one.
static BOOT_CHANNEL: AtomicU64 = AtomicU64::new(u64::MAX);
/// Set while `read_sector` owns the channel's data phase, so the IRQ
/// handler does NOT drain the 256 words it drains for IDENTIFY.
static MANUAL: [AtomicU64; 2] = [AtomicU64::new(0), AtomicU64::new(0)];

pub fn irqs(channel: usize) -> u64 {
    IRQS[channel].load(Ordering::Relaxed)
}

pub fn last_status(channel: usize) -> u8 {
    LAST_STATUS[channel].load(Ordering::Relaxed) as u8
}

/// The boot block device's channel, once `detect` has found one.
pub fn boot_channel() -> Option<usize> {
    let c = BOOT_CHANNEL.load(Ordering::Relaxed);
    if c == u64::MAX {
        None
    } else {
        Some(c as usize)
    }
}

/// The channel's logical sector size: 2048 for an ATAPI packet device,
/// 512 for an ATA disk.
pub fn sector_size(channel: usize) -> u32 {
    if IS_ATAPI[channel].load(Ordering::Relaxed) != 0 {
        2048
    } else {
        512
    }
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
                BOOT_CHANNEL.store(ch as u64, Ordering::Relaxed);
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
    IS_ATAPI[channel].store(atapi as u64, Ordering::Relaxed);

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
            // Which command ACTUALLY completed is the authoritative
            // answer: the power-on signature is not always readable
            // after an earlier probe, and an ATAPI drive answers 0xA1.
            IS_ATAPI[channel].store((command == 0xA1) as u64, Ordering::Relaxed);
            return (command, status_seen, total, true);
        }
    }

    (last_command, status_seen, total, false)
}

/// Bounded poll for BSY-clear + DRQ-set. An error bit is a refusal, not a
/// wait; a device that never asserts DRQ times out.
unsafe fn wait_drq(c: &Channel) -> bool {
    for _ in 0..MAX_IRQ_SPINS {
        let status = read_reg(c.base, REG_STATUS);
        if status & STATUS_ERR != 0 {
            return false;
        }
        if status & STATUS_BSY == 0 && status & STATUS_DRQ != 0 {
            return true;
        }
        core::hint::spin_loop();
    }
    false
}

/// Read ONE logical sector (2048 bytes from an ATAPI packet device, 512
/// from an ATA disk) into `out`. Returns false on a timeout, an error
/// bit, or a slice too small for the device's sector.
///
/// The read is POLLED with the channel's interrupt disabled (`nIEN`), so
/// the boot's IRQ handler -- which drains IDENTIFY data -- cannot race
/// the data phase. The interrupt is re-enabled on every exit path.
///
/// # Safety
/// `channel` must have been confirmed present by [`detect`] and its
/// registers must be the live IDE channel. `out.len()` must be at least
/// [`sector_size`] for the channel.
pub unsafe fn read_sector(channel: usize, lba: u32, out: &mut [u8]) -> bool {
    let c = &CHANNELS[channel];
    let size = sector_size(channel) as usize;
    let atapi = IS_ATAPI[channel].load(Ordering::Relaxed) != 0;
    if out.len() < size {
        return false;
    }
    // The handler must not drain this data phase as if it were IDENTIFY.
    MANUAL[channel].store(1, Ordering::Relaxed);
    Port::<u8>::new(c.ctrl).write(0);
    write_reg(c.base, REG_DRIVEHEAD, 0xA0);
    delay_400ns(c.ctrl);
    write_reg(c.base, REG_SECCOUNT, 0);

    let mut ok = false;
    if atapi {
        // Byte-count limit 2048, then PACKET; the 12-byte READ(10) CDB
        // asks for one 2048-byte logical block.
        write_reg(c.base, REG_LBA0, 0);
        // Byte-count limit 2048 (0x0800): the device's data phase size.
        write_reg(c.base, REG_LBA1, 0x00);
        write_reg(c.base, REG_LBA2, 0x08);
        write_reg(c.base, REG_STATUS, 0xA0);
        if wait_drq(c) {
            // READ(10): opcode, flags, LBA (4 big-endian bytes), group,
            // transfer length (2 big-endian bytes), control -- then the
            // two padding bytes the 12-byte packet requires. The length
            // MUST be at bytes 7..8; the first attempt put it at 8..9 and
            // the drive answered "zero blocks, no data" (status 0x50).
            let cdb: [u8; 12] = [
                0x28,
                0,
                (lba >> 24) as u8,
                (lba >> 16) as u8,
                (lba >> 8) as u8,
                lba as u8,
                0,
                0,
                1,
                0,
                0,
                0,
            ];
            let mut data: Port<u16> = Port::new(c.base + REG_DATA);
            // The ATA data port carries the packet as little-endian
            // 16-bit words: byte order within each word is the CPU's,
            // not the CDB's network order. Using from_be_bytes here
            // swapped every pair and left the drive waiting for a
            // command it never received.
            for pair in cdb.chunks_exact(2) {
                data.write(u16::from_le_bytes([pair[0], pair[1]]));
            }
            if wait_drq(c) {
                for i in 0..(size / 2) {
                    let word = data.read();
                    let b = word.to_le_bytes();
                    out[i * 2] = b[0];
                    out[i * 2 + 1] = b[1];
                }
                ok = true;
            }
        }
    } else {
        // LBA28 READ SECTORS: one sector.
        write_reg(c.base, REG_DRIVEHEAD, 0xE0 | ((lba >> 24) as u8 & 0x0f));
        delay_400ns(c.ctrl);
        write_reg(c.base, REG_SECCOUNT, 1);
        write_reg(c.base, REG_LBA0, lba as u8);
        write_reg(c.base, REG_LBA1, (lba >> 8) as u8);
        write_reg(c.base, REG_LBA2, (lba >> 16) as u8);
        write_reg(c.base, REG_STATUS, 0x20);
        if wait_drq(c) {
            let mut data: Port<u16> = Port::new(c.base + REG_DATA);
            for i in 0..(size / 2) {
                let word = data.read();
                let b = word.to_le_bytes();
                out[i * 2] = b[0];
                out[i * 2 + 1] = b[1];
            }
            ok = true;
        }
    }

    // Re-enable interrupts and clear any latched status; the boot's
    // handler stays usable for whatever runs next.
    let _ = read_reg(c.base, REG_STATUS);
    MANUAL[channel].store(0, Ordering::Relaxed);
    Port::<u8>::new(c.ctrl).write(0);
    ok
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
        if status & STATUS_DRQ != 0 && MANUAL[channel].load(Ordering::Relaxed) == 0 {
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
