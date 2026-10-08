//! Legacy 8259 Programmable Interrupt Controller (PIC) remapping.
//!
//! The PIC's power-on-default vector mapping puts IRQ0-7 at interrupt
//! vectors 0x08-0x0F and IRQ8-15 at 0x70-0x77 -- and 0x08-0x0F is
//! exactly where the CPU's own exception vectors live (0x08 = Double
//! Fault, 0x0D = General Protection Fault, ...). Enabling interrupts
//! without remapping first means a routine hardware IRQ gets
//! delivered as if it were a CPU exception (this bit the boot process
//! for real during Phase 1 bring-up -- see IMPLEMENTATION_STATUS.md
//! bug #7, which is why interrupts stayed masked from `_start` through
//! all of Phase 1 and Phase 2). This module is what finally makes it
//! safe to `sti`.
//!
//! Remaps IRQ0-7 to vectors 0x20-0x27 and IRQ8-15 to 0x28-0x2F --
//! clear of both the CPU exception range (0x00-0x1F) and each other.

use x86_64::instructions::port::Port;

const PIC1_COMMAND: u16 = 0x20;
const PIC1_DATA: u16 = 0x21;
const PIC2_COMMAND: u16 = 0xA0;
const PIC2_DATA: u16 = 0xA1;

const ICW1_INIT: u8 = 0x10;
const ICW1_ICW4: u8 = 0x01;
const ICW4_8086: u8 = 0x01;

pub const PIC1_OFFSET: u8 = 0x20; // IRQ0-7   -> vectors 0x20-0x27
pub const PIC2_OFFSET: u8 = 0x28; // IRQ8-15  -> vectors 0x28-0x2F

/// IRQ0, the Programmable Interval Timer -- the one IRQ Phase 3
/// unmasks. Every other line (keyboard, RTC, ATA, ...) stays masked
/// until a later phase actually needs it; unmasking an IRQ with no
/// handler wired up is worse than not supporting it at all (it would
/// either hang waiting for an EOI that never comes, or fall through to
/// a spurious-looking default handler).
pub const IRQ_TIMER: u8 = 0;

pub struct Pic {
    command: Port<u8>,
    data: Port<u8>,
}

pub struct ChainedPics {
    pic1: Pic,
    pic2: Pic,
}

impl ChainedPics {
    const fn new() -> Self {
        ChainedPics {
            pic1: Pic {
                command: Port::new(PIC1_COMMAND),
                data: Port::new(PIC1_DATA),
            },
            pic2: Pic {
                command: Port::new(PIC2_COMMAND),
                data: Port::new(PIC2_DATA),
            },
        }
    }

    /// Standard ICW1-ICW4 remap sequence, with a short I/O-port write
    /// (`io_wait`) between steps -- real hardware on real machines can
    /// be too slow to keep up with back-to-back port writes here;
    /// QEMU doesn't strictly need it, but it's cheap and it's what
    /// makes this correct on real hardware too, not just in emulation.
    unsafe fn remap(&mut self) {
        let mut wait_port: Port<u8> = Port::new(0x80); // unused POST-diagnostic port, a conventional no-op delay
        let mut io_wait = || unsafe { wait_port.write(0) };

        let saved_mask1 = self.pic1.data.read();
        let saved_mask2 = self.pic2.data.read();

        self.pic1.command.write(ICW1_INIT | ICW1_ICW4);
        io_wait();
        self.pic2.command.write(ICW1_INIT | ICW1_ICW4);
        io_wait();

        self.pic1.data.write(PIC1_OFFSET);
        io_wait();
        self.pic2.data.write(PIC2_OFFSET);
        io_wait();

        self.pic1.data.write(4); // tell PIC1 it has a slave at IRQ2
        io_wait();
        self.pic2.data.write(2); // tell PIC2 its cascade identity
        io_wait();

        self.pic1.data.write(ICW4_8086);
        io_wait();
        self.pic2.data.write(ICW4_8086);
        io_wait();

        // Restore the caller's mask rather than assuming "all masked"
        // or "all unmasked" -- remap() only repositions vectors, mask
        // state is a separate, deliberate decision made by init().
        self.pic1.data.write(saved_mask1);
        self.pic2.data.write(saved_mask2);
    }

    fn mask_all(&mut self) {
        unsafe {
            self.pic1.data.write(0xFF);
            self.pic2.data.write(0xFF);
        }
    }

    fn set_mask(&mut self, irq: u8, masked: bool) {
        let (port, bit) = if irq < 8 {
            (&mut self.pic1.data, irq)
        } else {
            (&mut self.pic2.data, irq - 8)
        };
        unsafe {
            let current = port.read();
            let updated = if masked {
                current | (1 << bit)
            } else {
                current & !(1 << bit)
            };
            port.write(updated);
        }
    }

    /// Must be called at the end of every IRQ handler. For IRQ8-15
    /// (the slave PIC), both PICs need an EOI, since the slave's
    /// signal itself arrives at the master via the cascade line
    /// (IRQ2) and both controllers are tracking "am I mid-interrupt".
    fn end_of_interrupt(&mut self, irq: u8) {
        unsafe {
            if irq >= 8 {
                self.pic2.command.write(0x20);
            }
            self.pic1.command.write(0x20);
        }
    }
}

pub static PICS: spin::Mutex<ChainedPics> = spin::Mutex::new(ChainedPics::new());

/// MUST be called exactly once, during
/// `crate::boot_phase::BootPhase::InterruptControllerInit`, after the
/// IDT is loaded (so a stray/misrouted IRQ during the brief remap
/// window at least has *something* installed for its new vector,
/// rather than immediately double-faulting) and before `sti` is ever
/// executed anywhere.
pub fn init() {
    let mut pics = PICS.lock();
    pics.mask_all(); // deliberately mask everything before remapping
    unsafe { pics.remap() };
    pics.mask_all(); // and again after -- remap() preserves whatever
                      // mask existed before it ran, which at this point
                      // is still "everything", but this makes the
                      // invariant explicit rather than relying on that
    pics.set_mask(IRQ_TIMER, false); // ... then unmask exactly IRQ0
}

pub fn end_of_interrupt(irq: u8) {
    PICS.lock().end_of_interrupt(irq);
}

/// Unmasks one IRQ line. Added for the RTC (IRQ8) proof in
/// [`crate::rtc`]: unmasking a line only makes sense once a real
/// handler for its vector is installed and understood, which is why
/// this wasn't exposed during Phase 3 when every non-timer line stayed
/// masked. Note that an IRQ on the slave PIC (8-15) additionally needs
/// the cascade line (IRQ2) unmasked on the master for its signal to
/// reach the CPU at all -- see `crate::rtc::IRQ_CASCADE`.
pub fn unmask(irq: u8) {
    PICS.lock().set_mask(irq, false);
}
