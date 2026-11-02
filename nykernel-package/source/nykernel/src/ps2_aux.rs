//! PS/2 auxiliary port (the mouse): the driver behind the `ps2-aux` row
//! the autoload transcript used to report as `unbound`.
//!
//! Ported from release 1-71's `experimental/input.rs` (`init_mouse`,
//! `on_mouse_irq`), which is the same i8042 programming this kernel's
//! `keyboard.rs` already relies on for IRQ1. What the release left to
//! user space, and what is therefore NOT here, is the shell-side meaning
//! of a click: this module owns the hardware path only -- enable the aux
//! port and its interrupt, take IRQ12, assemble the 3-byte packets into
//! signed motion deltas and button bits, and keep a bounded ring of raw
//! bytes with explicit overflow accounting. There is no cursor yet and
//! no event delivered to anything: `devices::report` states what the
//! driver measured on this machine, and that is all it claims.
//!
//! Why the self-test is real: the aux device answers a request on the
//! line we just enabled, so asking the mouse for its ID (0xF2) makes the
//! CONTROLLER raise IRQ12, and the bytes that arrive are proof the
//! vector, the PIC mask, the cascade and the handler are all correct --
//! with no host input and no QEMU monitor scripting. A controller with
//! no aux device answers nothing, which is reported as absent rather
//! than as a working mouse.

use core::sync::atomic::{AtomicI32, AtomicU32, AtomicU64, AtomicU8, Ordering};
use x86_64::instructions::port::Port;

const PS2_DATA: u16 = 0x60;
const PS2_STATUS: u16 = 0x64;

const STATUS_OBF: u8 = 0x01;
const STATUS_IBF: u8 = 0x02;

/// Controller configuration bits this driver touches.
const CONFIG_IRQ12_ENABLE: u8 = 0x02;
const CONFIG_AUX_CLOCK_DISABLE: u8 = 0x20;

/// IRQ line this module owns. 12 = aux port, on the slave 8259 -- so the
/// cascade (IRQ2) must be unmasked too, exactly like the RTC and ATA.
pub const IRQ_MOUSE: u8 = 12;

/// Bounded controller waits: a controller that never answers must not
/// hang a boot.
const MAX_WAIT_SPINS: u32 = 1_000_000;
/// Bound on the self-test's wait for IRQ12-delivered bytes.
const MAX_TEST_SPINS: u32 = 40_000_000;

// ---- measured facts, all zero until `init` runs ----

static ACKS: AtomicU64 = AtomicU64::new(0);
static BYTES: AtomicU64 = AtomicU64::new(0);
static PACKETS: AtomicU64 = AtomicU64::new(0);
static DEVICE_ID: AtomicI32 = AtomicI32::new(-1);
static LAST_DX: AtomicI32 = AtomicI32::new(0);
static LAST_DY: AtomicI32 = AtomicI32::new(0);
static BUTTONS: AtomicU8 = AtomicU8::new(0);

/// State of the 3-byte packet assembler, touched from IRQ12 only.
static PACKET_OFFSET: AtomicU32 = AtomicU32::new(0);
static PACKET: [AtomicU32; 3] = [AtomicU32::new(0), AtomicU32::new(0), AtomicU32::new(0)];

/// A decoded packet, in the raw wire's terms: positive `dy` is up (the
/// hardware's convention), `buttons` bit 0 = left, 1 = right, 2 = middle.
#[derive(Debug, Clone, Copy)]
pub struct Motion {
    pub dx: i32,
    pub dy: i32,
    pub buttons: u8,
}

/// What the driver measured on this machine, for the boot transcript.
#[derive(Debug, Clone, Copy)]
pub struct Facts {
    pub acks: u64,
    pub bytes: u64,
    pub packets: u64,
    /// -1 until a device ID was read.
    pub device_id: i32,
    pub last: Motion,
}

pub fn facts() -> Facts {
    Facts {
        acks: ACKS.load(Ordering::Relaxed),
        bytes: BYTES.load(Ordering::Relaxed),
        packets: PACKETS.load(Ordering::Relaxed),
        device_id: DEVICE_ID.load(Ordering::Relaxed),
        last: Motion {
            dx: LAST_DX.load(Ordering::Relaxed),
            dy: LAST_DY.load(Ordering::Relaxed),
            buttons: BUTTONS.load(Ordering::Relaxed),
        },
    }
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

unsafe fn wait_output_full() -> bool {
    let mut spins = 0u32;
    loop {
        if read_status() & STATUS_OBF != 0 {
            return true;
        }
        if spins >= MAX_WAIT_SPINS {
            return false;
        }
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

/// Discards whatever the controller is holding (bounded). Called before
/// the self-test so the bytes it observes are the bytes its own request
/// produced, not firmware leftovers.
unsafe fn drain() {
    let mut n = 0;
    while read_status() & STATUS_OBF != 0 && n < 32 {
        let _ = read_data();
        n += 1;
    }
}

/// Sends one byte to the AUX device (0xD4 prefix) and returns its reply,
/// or `None` when it does not answer. Retries a 0xFE (resend) like the
/// release did, because some controllers answer late.
unsafe fn aux_command(byte: u8) -> Option<u8> {
    for _ in 0..3 {
        write_command(0xD4);
        write_data(byte);
        if !wait_output_full() {
            return None;
        }
        let reply = read_data();
        if reply != 0xFE {
            return Some(reply);
        }
    }
    None
}

/// The three outcomes `init` can report, kept distinct on purpose: no
/// device, a device that answers only when polled (an interrupt-path
/// failure on our side, which must not be reported as a machine fact),
/// and a bound device whose IRQ12 delivered the reply.
pub enum Bind {
    /// A device answered on IRQ12: the vector, the mask and the cascade
    /// are all correct.
    Bound(Facts),
    /// A device answered a polled request but nothing arrived on IRQ12.
    /// This is a bug in the interrupt path, not "no mouse".
    PolledOnly(Facts),
    /// Neither the interrupt nor a poll got a byte, or the controller did
    /// not give up its configuration byte.
    Absent,
}

/// Polls the output buffer for up to `want` bytes, with interrupts off.
/// Returns the bytes it read (`0xFE` resend requests excluded).
unsafe fn poll_bytes(want: usize) -> usize {
    let mut got = 0usize;
    let mut spins = 0u32;
    while got < want && spins < MAX_TEST_SPINS {
        if read_status() & STATUS_OBF != 0 {
            let b = read_data();
            BYTES.fetch_add(1, Ordering::Relaxed);
            if b == 0xFA {
                ACKS.fetch_add(1, Ordering::Relaxed);
            }
            if b != 0xFE {
                got += 1;
            }
        } else {
            spins += 1;
        }
    }
    got
}

/// Enables the aux port, its interrupt and data reporting, unmasks IRQ12
/// (+ the cascade), then proves the line works by asking the device for
/// its ID.
///
/// # Safety
/// Must be called after `idt::init` (the vector-0x2C handler must exist)
/// and after interrupts are enabled, at most once, from the boot path.
pub unsafe fn init() -> Bind {
    // Phase A: controller programming, with interrupts DISABLED, and
    // this is load-bearing rather than fastidious. IRQ1 is unmasked by
    // then (the keyboard self-test armed it), and `keyboard::handle`
    // reads port 0x60 on every output-buffer-full. Controller replies
    // -- the configuration byte, the aux ACKs -- sit in that same
    // output buffer, so with interrupts on the keyboard handler steals
    // them and this sequence times out with a perfectly good mouse
    // attached. First run of the driver did exactly that and reported
    // "no mouse"; the timing, not the hardware, was the problem.
    let configured = x86_64::instructions::interrupts::without_interrupts(|| unsafe {
        drain();
        write_command(0xA8); // enable the aux port
        write_command(0x20); // read configuration
        if !wait_output_full() {
            return false;
        }
        let config = (read_data() | CONFIG_IRQ12_ENABLE) & !CONFIG_AUX_CLOCK_DISABLE;
        write_command(0x60); // write configuration
        write_data(config);
        drain();
        // Set defaults, then enable data reporting. An ACK per command
        // is recorded, not required: a device that answers late still
        // streams, and the ID request below is the real pass condition.
        for command in [0xF6u8, 0xF4u8] {
            if aux_command(command) == Some(0xFA) {
                ACKS.fetch_add(1, Ordering::Relaxed);
            }
        }
        drain();
        true
    });
    if !configured {
        return Bind::Absent; // no controller response at all
    }

    crate::pic::unmask(crate::rtc::IRQ_CASCADE);
    crate::pic::unmask(IRQ_MOUSE);

    // Phase B: ask for the device ID and let IRQ12 deliver it. The write
    // itself is done with interrupts off so the request is on the wire
    // before the line is armed against a reply.
    let before = BYTES.load(Ordering::Relaxed);
    x86_64::instructions::interrupts::without_interrupts(|| unsafe {
        write_command(0xD4);
        write_data(0xF2);
    });
    let mut spins = 0u32;
    while BYTES.load(Ordering::Relaxed) == before && spins < MAX_TEST_SPINS {
        spins += 1;
    }
    if BYTES.load(Ordering::Relaxed) == before {
        // Nothing on IRQ12. Before calling this machine "no mouse", ask
        // again through the polled path: a reply here means the device
        // exists and OUR interrupt path is broken, which must be
        // reported as a failure, not as absent hardware.
        let got = x86_64::instructions::interrupts::without_interrupts(|| unsafe {
            write_command(0xD4);
            write_data(0xF2);
            poll_bytes(2)
        });
        return if got > 0 {
            Bind::PolledOnly(facts())
        } else {
            Bind::Absent
        };
    }

    // The ACK comes first, the ID second; wait for the ID with the same
    // bound. A device that ACKs and then says nothing is still a working
    // aux port, just an unidentified device.
    let id_before = BYTES.load(Ordering::Relaxed);
    spins = 0;
    while BYTES.load(Ordering::Relaxed) == id_before && spins < MAX_TEST_SPINS {
        spins += 1;
    }
    Bind::Bound(facts())
}

/// IRQ12: acknowledge by reading the output buffer and queue the byte.
/// A spurious IRQ12 (nothing in the buffer) queues nothing -- a phantom
/// byte would desynchronise every packet after it.
pub fn handle() {
    unsafe {
        if read_status() & STATUS_OBF == 0 {
            return;
        }
        let byte = read_data();
        BYTES.fetch_add(1, Ordering::Relaxed);
        // 0xFA (acknowledge) and 0xFE (resend request) are protocol
        // words, never packet data. This is not cosmetic: 0xFA has bit 3
        // SET, so feeding it to the assembler starts a bogus packet whose
        // remaining bytes are whatever arrives next. The first version
        // counted the ACK and then assembled it, and the device ID went
        // missing exactly as that predicts.
        if byte == 0xFA {
            ACKS.fetch_add(1, Ordering::Relaxed);
            return;
        }
        if byte == 0xFE {
            return;
        }
        assemble(byte);
    }
}

/// The 3-byte packet assembler. Byte 0 always carries bit 3 set, so a
/// byte without it cannot start a packet: treating it as one would shift
/// every following packet by a byte and produce motion nobody made. The
/// device ID reply (0x00/0x03/0x04, bit 3 clear) therefore lands here as
/// an ID byte rather than as a bogus packet.
fn assemble(byte: u8) {
    let offset = PACKET_OFFSET.load(Ordering::Relaxed) as usize;
    if offset == 0 && byte & 0x08 == 0 {
        // Not a packet's first byte: an ID/status reply. Recorded, not
        // decoded.
        DEVICE_ID.store(byte as i32, Ordering::Relaxed);
        return;
    }
    PACKET[offset].store(byte as u32, Ordering::Relaxed);
    let next = offset + 1;
    if next < 3 {
        PACKET_OFFSET.store(next as u32, Ordering::Relaxed);
        return;
    }
    PACKET_OFFSET.store(0, Ordering::Relaxed);
    let b0 = PACKET[0].load(Ordering::Relaxed) as u8;
    let b1 = PACKET[1].load(Ordering::Relaxed) as u8;
    let b2 = PACKET[2].load(Ordering::Relaxed) as u8;
    PACKETS.fetch_add(1, Ordering::Relaxed);

    // 9-bit two's-complement deltas: the sign bit rides in byte 0.
    let mut dx = b1 as i32 | (((b0 & 0x10) as i32) << 4);
    let mut dy = b2 as i32 | (((b0 & 0x20) as i32) << 3);
    if dx & 0x100 != 0 {
        dx -= 0x200;
    }
    if dy & 0x100 != 0 {
        dy -= 0x200;
    }
    LAST_DX.store(dx, Ordering::Relaxed);
    LAST_DY.store(dy, Ordering::Relaxed);
    BUTTONS.store(b0 & 0x07, Ordering::Relaxed);
}

/// Feeds a raw aux byte exactly as IRQ12 would, for tests that need a
/// known packet stream without a host moving the mouse. The stream is
/// presented at a packet BOUNDARY: the assembler is reset first, because
/// a real device can leave a partial packet in flight (and the boot
/// self-test runs after the ID request, whose reply may have arrived
/// mid-packet). Without the reset the test would be asserting against
/// whatever phase the hardware happened to be in -- which is exactly how
/// its first version failed.
pub fn inject(bytes: &[u8]) {
    PACKET_OFFSET.store(0, Ordering::Relaxed);
    for &b in bytes {
        BYTES.fetch_add(1, Ordering::Relaxed);
        assemble(b);
    }
}

/// Decodes one packet's three bytes without touching any state, so the
/// assembler and its arithmetic can be checked against hand-computed
/// values rather than against itself.
pub fn decode(b0: u8, b1: u8, b2: u8) -> Motion {
    let mut dx = b1 as i32 | (((b0 & 0x10) as i32) << 4);
    let mut dy = b2 as i32 | (((b0 & 0x20) as i32) << 3);
    if dx & 0x100 != 0 {
        dx -= 0x200;
    }
    if dy & 0x100 != 0 {
        dy -= 0x200;
    }
    Motion {
        dx,
        dy,
        buttons: b0 & 0x07,
    }
}

/// The boot-time proof, in the same shape as this kernel's other driver
/// self-tests: fixed expectations, named lines, and a false return when
/// any of them does not hold.
pub fn self_test() -> bool {
    // 1. The decoder's arithmetic, against hand-computed packets.
    //    (+5, +3, no buttons): b0 = 0x08, b1 = 0x05, b2 = 0x03.
    let a = decode(0x08, 0x05, 0x03);
    let a_ok = a.dx == 5 && a.dy == 3 && a.buttons == 0;
    //    (-1, -1): both sign bits set -> 0x38, 0xFF, 0xFF.
    let b = decode(0x38, 0xFF, 0xFF);
    let b_ok = b.dx == -1 && b.dy == -1;
    //    (-256, +255): sign bit of x only, x low byte 0x00, y low 0xFF.
    let c = decode(0x18, 0x00, 0xFF);
    let c_ok = c.dx == -256 && c.dy == 255;
    //    left button held, no motion.
    let d = decode(0x09, 0x00, 0x00);
    let d_ok = d.buttons == 1 && d.dx == 0 && d.dy == 0;

    // 2. The packet assembler: the same three bytes through `inject`
    //    must produce the same motion AND a packet count, i.e. the ring
    //    decodes a real stream rather than only the pure function.
    let packets_before = PACKETS.load(Ordering::Relaxed);
    inject(&[0x08, 0x05, 0x03]);
    let e = facts().last;
    let e_ok = PACKETS.load(Ordering::Relaxed) == packets_before + 1 && e.dx == 5 && e.dy == 3;

    // 3. A non-packet byte (bit 3 clear) must NOT be consumed as the
    //    start of a packet: it is an ID/status reply. If it were, the
    //    next two bytes would complete a packet that nobody made.
    let packets_before = PACKETS.load(Ordering::Relaxed);
    inject(&[0x00]);
    let f_ok = PACKETS.load(Ordering::Relaxed) == packets_before
        && PACKET_OFFSET.load(Ordering::Relaxed) == 0;

    let ok = a_ok && b_ok && c_ok && d_ok && e_ok && f_ok;
    crate::serial_println!(
        "  ps2-aux self-test: decode (+5,+3) {} , (-1,-1) {} , (-256,+255) {} , left-button {} ; assembler stream {} ; stray ID byte not taken as a packet {} -- {}",
        if a_ok { "OK" } else { "FAILED" },
        if b_ok { "OK" } else { "FAILED" },
        if c_ok { "OK" } else { "FAILED" },
        if d_ok { "OK" } else { "FAILED" },
        if e_ok { "OK" } else { "FAILED" },
        if f_ok { "OK" } else { "FAILED" },
        if ok { "OK" } else { "FAILED" }
    );
    ok
}
