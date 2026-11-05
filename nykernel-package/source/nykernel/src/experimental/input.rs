//! The raw input byte ring -- the release's `experimental/input.rs` core
//! (1-50 shape), ported to THIS tree.
//!
//! Source: `porting/release-1-71/src/experimental/input.rs`. The release's
//! rule is kept exactly: the KERNEL DOES NOT DECODE. Scancode-set-1 and the
//! 3-byte PS/2 mouse packet decoder live in user space there (and this
//! tree's `ps2_aux` keeps its own assembler): what is ported here is the
//! part only the kernel can do -- the IRQ handlers hand the byte to the
//! kernel tagged with its device, into a lock-free SPSC ring, and the
//! `0x84` gate's `INPUT_POLL` row hands the oldest RAW byte to the holder
//! of an Input capability (see `caps::Kind::Input` and
//! `service84::CALL_INPUT_POLL`).
//!
//! Each of its release clauses, kept:
//! * a raw ring (fixed, bounded): a byte is never silently dropped -- the
//!   overflow count is reported (`dropped()`);
//! * single producer (the IRQ context) / single consumer (`INPUT_POLL`);
//! * every byte carries its device tag and its IRQ-time timestamp, and
//!   nothing arrives decoded;
//! * the boot-test-only `seed_*` functions queue *known* raw bytes so a
//!   proof can drive the poll row deterministically, exactly what the
//!   release's scripted demos did.
//!
//! NOT ported from the release, named so the absence is checkable: its
//! USB-HID reap hook (`push_mouse_byte`'s 1-66 caller, `usb.rs::poll()` --
//! this tree has no USB host controller to reap) and its live listen
//! window; the kernel-side self-test (`run()`) proves the ring's clauses
//! on seeded bytes, and the 0x84 proof drives the row through two real
//! IRQ-injected scancodes (see `svc84_proof`).

use core::sync::atomic::{AtomicU64, AtomicUsize, Ordering};

use alloc::vec::Vec;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DeviceKind {
    Ps2Keyboard,
    Ps2Mouse,
}

/// One raw byte from a PS/2 device, exactly as the controller delivered
/// it (NOTHING is decoded in the kernel).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct InputEvent {
    pub device: DeviceKind,
    pub byte: u8,
    pub timestamp_ticks: u64, // timer ticks at IRQ time
}

const QUEUE_LEN: usize = 1024; // raw bytes; the release's own capacity
static mut QUEUE: [Option<InputEvent>; QUEUE_LEN] = [None; QUEUE_LEN];
static HEAD: AtomicUsize = AtomicUsize::new(0); // next write (IRQ)
static TAIL: AtomicUsize = AtomicUsize::new(0); // next read (consumer)
static DROPPED: AtomicU64 = AtomicU64::new(0);

pub fn dropped() -> u64 {
    DROPPED.load(Ordering::Relaxed)
}

fn push(ev: InputEvent) {
    let head = HEAD.load(Ordering::Relaxed);
    let next = (head + 1) % QUEUE_LEN;
    if next == TAIL.load(Ordering::Acquire) {
        DROPPED.fetch_add(1, Ordering::Relaxed); // full: counted, never silent
        return;
    }
    unsafe { QUEUE[head] = Some(ev) };
    HEAD.store(next, Ordering::Release);
}

/// Called from the keyboard IRQ path (`keyboard::handle`): the handler
/// has already read the byte out of the controller's output buffer
/// (acknowledging the interrupt); queue it RAW, tagged with its device.
pub fn push_keyboard_byte(byte: u8) {
    push(InputEvent { device: DeviceKind::Ps2Keyboard, byte, timestamp_ticks: crate::idt::timer_ticks() });
}

/// Same contract for the aux line (`ps2_aux::handle`), for a mouse byte
/// exactly as the controller delivered it -- protocol words included (the
/// user-space assembler is what filters them; the kernel's raw stream
/// reports, never decides).
pub fn push_mouse_byte(byte: u8) {
    push(InputEvent { device: DeviceKind::Ps2Mouse, byte, timestamp_ticks: crate::idt::timer_ticks() });
}

/// Pops the oldest event, if any (single consumer).
pub fn pop() -> Option<InputEvent> {
    let tail = TAIL.load(Ordering::Relaxed);
    if tail == HEAD.load(Ordering::Acquire) {
        return None;
    }
    let ev = unsafe { QUEUE[tail] };
    TAIL.store((tail + 1) % QUEUE_LEN, Ordering::Release);
    ev
}

/// BOOT-TEST ONLY: empties the queue, then queues raw scancode-set-1
/// bytes (exactly what IRQ1 does per byte), so a poll test has a known
/// byte stream without a host injecting keys. Returns how many bytes
/// were queued (the decoding happens in user space).
pub fn seed_scancodes(bytes: &[u8]) -> usize {
    while pop().is_some() {}
    for &b in bytes {
        push_keyboard_byte(b);
    }
    bytes.len()
}

/// BOOT-TEST ONLY: queues raw PS/2 MOUSE bytes (what IRQ12 does per
/// byte); does not clear the queue. Returns the bytes queued.
pub fn seed_mouse_bytes(bytes: &[u8]) -> usize {
    for &b in bytes {
        push_mouse_byte(b);
    }
    bytes.len()
}

// ---- the kernel-side self-test ------------------------------------------

/// The release's own ring clauses, positive AND negative, on this port:
/// FIFO order and device tagging, and overflow accounting (a full ring
/// reports its drops, never silently loses a byte).
pub fn run() -> bool {
    use crate::serial_println;

    let mut failed: Vec<&'static str> = Vec::new();
    let mut groups: u32 = 0;
    macro_rules! check {
        ($name:literal, $cond:expr) => {{
            groups += 1;
            if !$cond {
                failed.push($name);
            }
        }};
    }

    // 1. FIFO order and device tagging of the raw ring (no decoding
    //    exists in the kernel: the bytes come back IN ORDER, with the
    //    tags they were pushed under).
    while pop().is_some() {}
    check!(
        "fifo seeded both devices",
        seed_scancodes(&[0x1E, 0x9E, 0xE0, 0x4D]) == 4 && seed_mouse_bytes(&[0x08, 0x01]) == 2
    );
    let want = [
        (DeviceKind::Ps2Keyboard, 0x1Eu8),
        (DeviceKind::Ps2Keyboard, 0x9E),
        (DeviceKind::Ps2Keyboard, 0xE0),
        (DeviceKind::Ps2Keyboard, 0x4D),
        (DeviceKind::Ps2Mouse, 0x08),
        (DeviceKind::Ps2Mouse, 0x01),
    ];
    let mut fifo_ok = true;
    for (d, b) in want {
        fifo_ok &= matches!(pop(), Some(ev) if ev.device == d && ev.byte == b && ev.timestamp_ticks > 0);
    }
    fifo_ok &= pop().is_none();
    check!("fifo order + device tags + timestamps", fifo_ok);

    // 2. Overflow accounting: fill past capacity, none silently lost.
    let before = dropped();
    for i in 0..(QUEUE_LEN as u16 + 5) {
        push(InputEvent { device: DeviceKind::Ps2Keyboard, byte: i as u8, timestamp_ticks: 0 });
    }
    let mut got = 0u64;
    while pop().is_some() {
        got += 1;
    }
    let overflow_ok = got == (QUEUE_LEN as u64 - 1) && dropped() - before == 6;
    check!("overflow counted, not silent", overflow_ok);

    if failed.is_empty() {
        serial_println!(
            "  experimental input: {} raw-ring clause groups proven positive+negative (FIFO + device tags + IRQ-time timestamps, bounded queue with counted overflow, no decoding in the kernel) -- OK",
            groups
        );
        true
    } else {
        serial_println!("  experimental input: FAILED clauses {:?} -- raw-ring semantics do not hold", failed);
        false
    }
}
