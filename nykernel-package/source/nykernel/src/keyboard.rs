//! PS/2 keyboard (i8042) IRQ1 -- a real input IRQ line with a real
//! handler, proven deterministically without any human input.
//!
//! Closes the "keyboard IRQ" half of the "more IRQ lines with real
//! handlers" gap named in IMPLEMENTATION_STATUS.md. An input IRQ looks
//! untestable automatically -- it "only fires on human input" -- but
//! that reasoning is about needing a *real* keystroke, not about the
//! keyboard line being untestable in principle. The 8042 keyboard
//! controller can be told to
//! **write a byte straight into its output buffer** (command `0xD2`,
//! "Write Keyboard Output Buffer"); with the keyboard interrupt
//! enabled, that raises IRQ1 exactly as a real keystroke would. So the
//! guest can inject a known scancode and observe its own handler
//! receive it -- a genuine, input-free, every-boot proof.
//!
//! (This is the same controller-conformance property a PC firmware
//! self-test relies on; it uses no external input and no QEMU monitor
//! scripting, so it works in a headless automated run.)
//!
//! IRQ1 is a master-PIC line, so unlike the RTC it needs no cascade:
//! `pic::unmask(IRQ_KEYBOARD)` alone is enough once a handler is
//! installed. Per `pic.rs`'s rule, the handler is installed by
//! `idt::init` *before* the line is ever unmasked.

use core::sync::atomic::{AtomicU64, Ordering};
use x86_64::instructions::port::Port;

const PS2_DATA: u16 = 0x60; // read: output buffer; write: data to device
const PS2_STATUS: u16 = 0x64; // read: status; write: command

/// Output Buffer Full -- a byte is waiting at `PS2_DATA`.
const STATUS_OBF: u8 = 0x01;
/// Input Buffer Full -- the controller has not consumed the last write yet.
const STATUS_IBF: u8 = 0x02;

const CMD_READ_CONFIG: u8 = 0x20;
const CMD_WRITE_CONFIG: u8 = 0x60;
const CMD_ENABLE_KEYBOARD: u8 = 0xAE;
/// Write the next data-port byte directly into the controller's output
/// buffer (and therefore raise IRQ1 if enabled) -- the injection trick
/// this module's self-test is built on.
const CMD_WRITE_OUTBUF: u8 = 0xD2;
/// Controller-configuration bit 0: enable the keyboard interrupt (IRQ1).
const CONFIG_IRQ1_ENABLE: u8 = 0x01;

/// IRQ line this module owns. 1 = keyboard on the master 8259.
pub const IRQ_KEYBOARD: u8 = 1;

/// Bounded waits, so a controller that never responds can't hang the
/// boot forever -- a timeout is a reportable failure, not an infinite
/// silent spin.
const MAX_WAIT_SPINS: u32 = 1_000_000;

/// Incremented once per keyboard IRQ handled.
static SCANCODES: AtomicU64 = AtomicU64::new(0);
/// The most recently read scancode (`0xFF` until one arrives).
static LAST_SCANCODE: AtomicU64 = AtomicU64::new(0xFF);

pub fn scancodes_received() -> u64 {
    SCANCODES.load(Ordering::Relaxed)
}

pub fn last_scancode() -> u8 {
    LAST_SCANCODE.load(Ordering::Relaxed) as u8
}

unsafe fn read_status() -> u8 {
    Port::<u8>::new(PS2_STATUS).read()
}

unsafe fn read_data() -> u8 {
    Port::<u8>::new(PS2_DATA).read()
}

unsafe fn wait_input_empty() {
    let mut spins = 0u32;
    while read_status() & STATUS_IBF != 0 && spins < MAX_WAIT_SPINS {
        spins += 1;
    }
}

unsafe fn wait_output_full() {
    let mut spins = 0u32;
    while read_status() & STATUS_OBF == 0 && spins < MAX_WAIT_SPINS {
        spins += 1;
    }
}

unsafe fn write_command(byte: u8) {
    wait_input_empty();
    Port::<u8>::new(PS2_STATUS).write(byte);
}

unsafe fn write_data(byte: u8) {
    wait_input_empty();
    Port::<u8>::new(PS2_DATA).write(byte);
}

/// Reads and discards whatever is currently in the output buffer
/// (bounded, so a stuck OBF bit can't loop forever). Used to clear any
/// power-on/stale byte before the self-test so the injected scancode is
/// unambiguously the one observed.
unsafe fn drain() {
    let mut n = 0;
    while read_status() & STATUS_OBF != 0 && n < 16 {
        let _ = read_data();
        n += 1;
    }
}

/// Enables the keyboard interface and the keyboard interrupt (IRQ1) at
/// the controller. Does NOT touch the PIC mask -- `kernel_main` unmasks
/// IRQ1 only after `idt::init` has installed the vector-0x21 handler,
/// so there is no window where IRQ1 could fire with nothing to handle
/// it.
///
/// # Safety
/// Must be called after `idt::init` and at most once.
pub unsafe fn init() {
    // Clear any byte left in the output buffer (QEMU's emulated 8042,
    // like real hardware, may have a self-test byte waiting).
    drain();

    // Make sure the keyboard interface itself is enabled; firmware can
    // leave it disabled.
    write_command(CMD_ENABLE_KEYBOARD);
    drain();

    // Read the controller configuration byte, set the keyboard-IRQ
    // enable bit, and write it back. Reading the config puts it in the
    // output buffer *before* bit 0 is set, so this cannot itself raise
    // a spurious IRQ1: the line is only asserted once OBF is set *and*
    // the enable bit is set.
    write_command(CMD_READ_CONFIG);
    wait_output_full();
    let config = read_data();

    write_command(CMD_WRITE_CONFIG);
    write_data(config | CONFIG_IRQ1_ENABLE);
    drain();
}

/// Injects `scancode` through the controller's own output buffer, which
/// raises IRQ1 exactly as a real keystroke would. This is the guest
/// generating its own deterministic input -- no human, no QEMU monitor.
///
/// # Safety
/// IRQ1 must be unmasked (or about to be) with the vector-0x21 handler
/// installed, so the injected byte is consumed.
pub unsafe fn inject(scancode: u8) {
    write_command(CMD_WRITE_OUTBUF);
    write_data(scancode);
}

/// Called from the vector-0x21 handler on every keyboard interrupt:
/// read the scancode out of the output buffer (which also deasserts the
/// controller's IRQ line) and count it.
pub fn handle() {
    unsafe {
        if read_status() & STATUS_OBF != 0 {
            LAST_SCANCODE.store(read_data() as u64, Ordering::Relaxed);
        }
    }
    SCANCODES.fetch_add(1, Ordering::Relaxed);
}
