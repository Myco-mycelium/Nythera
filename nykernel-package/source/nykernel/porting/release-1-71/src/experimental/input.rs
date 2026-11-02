//! Phase 9, first increment: native input path (NPS-012) -- a PS/2
//! keyboard driver producing *normalized* input events.
//!
//! **Scope, stated plainly.** NPS-012 §3 requires native Xbox / PlayStation /
//! Switch-style controller support over USB and Bluetooth, normalized
//! identity (§3.3), delivery to the foreground container via CAP-INPUT
//! over the NPS-003 IPC path (§3.2), real-time scheduling eligibility
//! (§6.1) and IPC rate-limit exemptions (§6.2). **None of that is done
//! here**: there is no USB or Bluetooth stack, no controllers, no
//! containers/capabilities (NPS-002/003 are Draft), no scheduling
//! classes. This module is the smallest honest slice of the *shape*:
//! hardware IRQ -> decode -> normalized `InputEvent` {device, kind,
//! timestamp} -> bounded queue with explicit overflow accounting
//! (events are never silently dropped; the drop count is reported).
//! PS/2 keyboard because it is the only input device QEMU lets us drive
//! and verify without a USB stack. Experimental by the same rule as the
//! other NPS-002/003-adjacent work.
//!
//! **1-50: the decoders are NOT in the kernel any more.** Scancode-set-1 decoding (bit 7 = release, 0xE0 = extended prefix) and the 3-byte PS/2 mouse packet
//! assembler now live in USER SPACE (`userprog/ps2dec.inc`, included by the router `kbdrt`/`kbdlive` and by `inputsvc`). What remains here is the part only the
//! kernel can do: the IRQ handlers read the byte from the controller (acknowledging the interrupt) and push it, tagged with its device, into a lock-free
//! single-producer/single-consumer ring (no allocation, no locks in IRQ context); `INPUT_POLL` hands the oldest RAW byte to the holder of the Input capability.
//! Also still here (documented in RC-17): the i8042 controller setup for the mouse (`init_mouse`).

use core::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use x86_64::instructions::port::Port;

use crate::serial_println;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DeviceKind {
    Ps2Keyboard,
    Ps2Mouse,
}

/// One raw byte from a PS/2 device, exactly as the controller delivered it (NOTHING is decoded in the kernel).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct InputEvent {
    pub device: DeviceKind,
    pub byte: u8,
    pub timestamp_ticks: u64, // timer ticks at IRQ time
}

const QUEUE_LEN: usize = 1024; // raw bytes (1-50: the scripted boot demo queues 434 of them); power of two not required; fixed
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

/// 1-66: queue one raw PS/2 mouse byte synthesised by the USB HID driver (usb.rs).
pub fn push_mouse_byte(byte: u8) { push(InputEvent { device: DeviceKind::Ps2Mouse, byte, timestamp_ticks: crate::idt::timer_ticks() }); }

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

/// BOOT-TEST ONLY: empties the queue, then queues raw scancode-set-1 bytes (exactly what IRQ1 does per byte), so a user-space routing test has a known byte stream
/// without a host injecting keys. Returns how many bytes were queued (the decoding happens in user space).
pub fn seed_scancodes(bytes: &[u8]) -> usize {
    while pop().is_some() {}
    for &b in bytes { push(InputEvent { device: DeviceKind::Ps2Keyboard, byte: b, timestamp_ticks: crate::idt::timer_ticks() }); }
    bytes.len()
}

/// BOOT-TEST ONLY: queues raw PS/2 MOUSE bytes (what IRQ12 does per byte); does not clear the queue. Returns the bytes queued.
pub fn seed_mouse_bytes(bytes: &[u8]) -> usize {
    for &b in bytes { push(InputEvent { device: DeviceKind::Ps2Mouse, byte: b, timestamp_ticks: crate::idt::timer_ticks() }); }
    bytes.len()
}

fn i8042_wait_write() { let mut st: Port<u8> = Port::new(0x64); for _ in 0..100_000 { if unsafe { st.read() } & 2 == 0 { return; } } }
fn i8042_wait_read() -> bool { let mut st: Port<u8> = Port::new(0x64); for _ in 0..100_000 { if unsafe { st.read() } & 1 != 0 { return true; } } false }
/// LIVE session only: enables the PS/2 aux port, its interrupt, and mouse streaming; then unmasks IRQ12 (and the cascade, IRQ2). The ACK bytes are consumed here by polling,
/// before the IRQ line is unmasked, so they can never be mistaken for packet bytes. Returns false when the controller does not answer (no mouse).
pub fn init_mouse() -> bool {
    let mut acked = true;
    unsafe {
        let mut cmd: Port<u8> = Port::new(0x64); let mut data: Port<u8> = Port::new(0x60);
        while cmd.read() & 1 != 0 { let _ = data.read(); }              // drain stale bytes
        i8042_wait_write(); cmd.write(0xA8);                            // enable the aux port
        i8042_wait_write(); cmd.write(0x20); if !i8042_wait_read() { return false; }
        let cfg = (data.read() | 0x02) & !0x20;                          // enable IRQ12, enable the aux clock
        i8042_wait_write(); cmd.write(0x60); i8042_wait_write(); data.write(cfg);
        for b in [0xF6u8, 0xF4u8] {                                      // defaults, then enable streaming; each is ACKed with 0xFA
            // 1-64: tolerant -- up to 3 tries per command (0xFE = resend); a device that never ACKs (some VMs/real controllers answer late or not at all) does NOT
            // switch the mouse off: streaming usually works anyway, so IRQ12 is unmasked regardless and the result only reports whether an ACK was seen.
            let mut got = false;
            for _ in 0..3 {
                i8042_wait_write(); cmd.write(0xD4); i8042_wait_write(); data.write(b);
                if !i8042_wait_read() { continue; }
                let r = data.read();
                if r == 0xFA { got = true; break; }
            }
            if !got { acked = false; }
        }
    }
    crate::pic::unmask(2);                                               // the slave PIC's cascade input
    crate::pic::unmask(crate::pic::IRQ_MOUSE);
    acked
}
/// Called from the IRQ12 handler: acknowledge by reading the byte, queue it raw.
pub fn on_mouse_irq() {
    if unsafe { Port::<u8>::new(0x64).read() } & 1 == 0 { return; }   // 1-64: nothing in the output buffer (spurious IRQ12) -- do not queue a phantom byte
    let byte = unsafe { Port::<u8>::new(0x60).read() };
    push(InputEvent { device: DeviceKind::Ps2Mouse, byte, timestamp_ticks: crate::idt::timer_ticks() });
}

/// Called from the IRQ1 handler: acknowledge by reading the byte, queue it raw.
pub fn on_keyboard_irq() {
    let byte = unsafe { Port::<u8>::new(0x60).read() };
    push(InputEvent { device: DeviceKind::Ps2Keyboard, byte, timestamp_ticks: crate::idt::timer_ticks() });
}

/// How long (timer ticks, ~18.2/s at the PIT default) the demo listens
/// for hardware events before concluding nothing was injected.
const LISTEN_TICKS: u64 = 40;

pub fn run() -> bool {
    // 1. FIFO order and device tagging of the raw ring (no decoding exists in the kernel any more: the user-space decoders are tested by tools/check_shell.py's independent models).
    while pop().is_some() {}
    let fifo_ok = seed_scancodes(&[0x1E, 0x9E, 0xE0, 0x4D]) == 4 && seed_mouse_bytes(&[0x08, 0x01]) == 2 && {
        let want = [(DeviceKind::Ps2Keyboard, 0x1Eu8), (DeviceKind::Ps2Keyboard, 0x9E), (DeviceKind::Ps2Keyboard, 0xE0), (DeviceKind::Ps2Keyboard, 0x4D), (DeviceKind::Ps2Mouse, 0x08), (DeviceKind::Ps2Mouse, 0x01)];
        let mut ok = true;
        for (d, b) in want { ok &= matches!(pop(), Some(ev) if ev.device == d && ev.byte == b); }
        ok && pop().is_none()
    };
    let decoder_ok = fifo_ok;

    // 2. Queue overflow accounting: fill past capacity, none silently lost.
    let before = dropped();
    for i in 0..(QUEUE_LEN as u16 + 5) {
        push(InputEvent { device: DeviceKind::Ps2Keyboard, byte: i as u8, timestamp_ticks: 0 });
    }
    let mut got = 0u64;
    while pop().is_some() { got += 1; }
    let overflow_ok = got == (QUEUE_LEN as u64 - 1) && dropped() - before == 6;

    // 3. Hardware path: listen for real IRQ1 bytes (injected by the host
    //    in tools/input_test.sh; absent in the normal boot test).
    crate::pic::unmask(crate::pic::IRQ_KEYBOARD);
    let start = crate::idt::timer_ticks();
    let mut events = 0u32;
    while crate::idt::timer_ticks() - start < LISTEN_TICKS {
        while let Some(ev) = pop() {
            events += 1;
            serial_println!("  experimental input-demo: raw byte device={:?} {:#04x} at tick {}", ev.device, ev.byte, ev.timestamp_ticks);
        }
        x86_64::instructions::hlt();
    }
    while let Some(ev) = pop() { // drain any stragglers
        events += 1;
        serial_println!("  experimental input-demo: raw byte device={:?} {:#04x} at tick {}", ev.device, ev.byte, ev.timestamp_ticks);
    }

    let ok = decoder_ok && overflow_ok;
    serial_println!(
        "  experimental input-demo self-test: raw ring FIFO + device tags {}, overflow accounting {}, hardware bytes seen {} ({}) -- {}",
        if decoder_ok { "OK" } else { "FAILED" },
        if overflow_ok { "OK" } else { "FAILED" },
        events,
        if events == 0 { "none injected" } else { "via real IRQ1" },
        if ok { "OK" } else { "FAILED" }
    );
    ok
}
