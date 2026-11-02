//! A software pointer: the first thing in this tree that CONSUMES input.
//!
//! The PS/2 aux driver (`crate::ps2_aux`) decodes packets but, until now,
//! nothing turned a motion packet into anything visible -- which made
//! "the mouse is bound" a claim about a byte stream and nothing more.
//! This module closes that loop with the smallest honest step: motion
//! moves a pointer, the pointer is drawn into the same framebuffer the
//! shell renders into, and the pixels are read back to prove it.
//!
//! Scope, stated plainly: this is a POINTER, not a window-system cursor.
//! There is no hit testing, no click routing, no cursor themes, no
//! multiple pointers, and no compositing -- moves are processed by
//! whoever holds the framebuffer (the kernel's boot session today), not
//! by a user-space session owner. What is real: the hardware packet path
//! reaches a drawn, verified pixel.
//!
//! Saving the background is a fixed 12x18 cell rather than a redraw of
//! the scene: the pointer must be able to move without the rest of the
//! screen being repainted, and on a machine with no compositor the
//! pointer owns exactly the pixels it covers.

use core::sync::atomic::{AtomicI32, Ordering};

use crate::devices::framebuffer::Framebuffer;

/// The last position the pointer was drawn at, published for consumers
/// that live outside the framebuffer's borrow (the `0x84` input call).
/// Separate from the `Cursor` handle on purpose: a service call must not
/// need a mutable borrow of the display to say where the pointer is.
static LAST_X: AtomicI32 = AtomicI32::new(0);
static LAST_Y: AtomicI32 = AtomicI32::new(0);

pub fn last_position() -> (i32, i32) {
    (
        LAST_X.load(Ordering::Relaxed),
        LAST_Y.load(Ordering::Relaxed),
    )
}

pub const W: u32 = 12;
pub const H: u32 = 18;

const OUTLINE: u32 = 0x0000_0000;
const FILL: u32 = 0x00ff_ffff;

/// The arrow: 0 = transparent, 1 = outline, 2 = fill. A const table
/// rather than a drawing algorithm so the shape is auditable and the
/// pixel probes in the boot test can name exact cells.
const SHAPE: [[u8; W as usize]; H as usize] = [
    [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    [1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    [1, 2, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    [1, 2, 2, 1, 0, 0, 0, 0, 0, 0, 0, 0],
    [1, 2, 2, 2, 1, 0, 0, 0, 0, 0, 0, 0],
    [1, 2, 2, 2, 2, 1, 0, 0, 0, 0, 0, 0],
    [1, 2, 2, 2, 2, 2, 1, 0, 0, 0, 0, 0],
    [1, 2, 2, 2, 2, 2, 2, 1, 0, 0, 0, 0],
    [1, 2, 2, 2, 2, 2, 2, 2, 1, 0, 0, 0],
    [1, 2, 2, 2, 2, 2, 2, 2, 2, 1, 0, 0],
    [1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 1, 0],
    [1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 1],
    [1, 2, 2, 2, 2, 2, 1, 1, 0, 0, 0, 0],
    [1, 2, 2, 1, 2, 2, 1, 0, 0, 0, 0, 0],
    [1, 2, 1, 0, 1, 2, 2, 1, 0, 0, 0, 0],
    [1, 1, 0, 0, 1, 2, 2, 1, 0, 0, 0, 0],
    [0, 0, 0, 0, 1, 2, 2, 1, 0, 0, 0, 0],
    [0, 0, 0, 0, 1, 1, 1, 1, 0, 0, 0, 0],
];

pub struct Cursor {
    x: i32,
    y: i32,
    /// The pixels the pointer covered before it was drawn here, so a move
    /// restores exactly what was underneath instead of repainting.
    saved: [u32; (W * H) as usize],
    drawn: bool,
}

impl Cursor {
    pub fn new(x: i32, y: i32) -> Self {
        Cursor {
            x,
            y,
            saved: [0; (W * H) as usize],
            drawn: false,
        }
    }

    pub fn position(&self) -> (i32, i32) {
        (self.x, self.y)
    }

    /// Clamps the pointer's top-left so the whole arrow stays on screen.
    /// A pointer that can walk off the edge and leave a half-drawn arrow
    /// behind is a rendering bug waiting to be blamed on the driver.
    fn clamp(x: i32, y: i32, fb: &Framebuffer) -> (i32, i32) {
        let max_x = fb.width as i32 - W as i32;
        let max_y = fb.height as i32 - H as i32;
        (x.max(0).min(max_x.max(0)), y.max(0).min(max_y.max(0)))
    }

    /// Draws the pointer, saving what it covers.
    pub fn draw(&mut self, fb: &mut Framebuffer) {
        if self.drawn {
            return;
        }
        let (x, y) = Self::clamp(self.x, self.y, fb);
        self.x = x;
        self.y = y;
        for row in 0..H {
            for col in 0..W {
                let kind = SHAPE[row as usize][col as usize];
                if kind == 0 {
                    continue;
                }
                let px = x + col as i32;
                let py = y + row as i32;
                let idx = (row * W + col) as usize;
                self.saved[idx] = fb.get(px as u32, py as u32).unwrap_or(0);
                fb.put(
                    px as u32,
                    py as u32,
                    if kind == 2 { FILL } else { OUTLINE },
                );
            }
        }
        self.drawn = true;
        LAST_X.store(self.x, Ordering::Relaxed);
        LAST_Y.store(self.y, Ordering::Relaxed);
    }

    /// Restores the pixels the pointer covered.
    pub fn erase(&mut self, fb: &mut Framebuffer) {
        if !self.drawn {
            return;
        }
        for row in 0..H {
            for col in 0..W {
                let kind = SHAPE[row as usize][col as usize];
                if kind == 0 {
                    continue;
                }
                let idx = (row * W + col) as usize;
                fb.put(
                    (self.x + col as i32) as u32,
                    (self.y + row as i32) as u32,
                    self.saved[idx],
                );
            }
        }
        self.drawn = false;
    }

    /// Applies one motion packet's delta and redraws in place.
    pub fn move_by(&mut self, dx: i32, dy: i32, fb: &mut Framebuffer) {
        self.erase(fb);
        // Hardware +y is up; screen +y is down. This is the one place the
        // convention changes, and flipping it here keeps every other
        // module speaking screen coordinates.
        self.x += dx;
        self.y -= dy;
        self.draw(fb);
    }

    /// The pixel a probe should find where the arrow's body is, for
    /// read-back checks: 3px right and 4px down from the tip is interior
    /// fill in `SHAPE`.
    pub fn probe_point(&self) -> (u32, u32) {
        ((self.x + 3) as u32, (self.y + 4) as u32)
    }
}

/// The boot session's pointer: draw, prove the decode->cursor->pixels path
/// with a known packet, then spend a bounded window applying whatever the
/// host or a hand actually moves. Returns the synthetic proof's verdict.
///
/// Called AFTER the device-mode render and AFTER the autoload/device
/// transcript, so a boot log is complete before the two-second window
/// starts -- the first version ran this inside the render step and a gate
/// that screenshots on the render line raced the transcript.
pub fn boot_session(fb: &mut Framebuffer) -> bool {
    let mut ptr = Cursor::new((fb.width / 2) as i32, (fb.height / 2) as i32);
    ptr.draw(fb);
    let (x0, y0) = ptr.position();

    // A packet through the REAL assembler: this pins the whole path from
    // bytes to pixels, including the +y flip and the background restore.
    crate::ps2_aux::inject(&[0x08, 150, 90]);
    let m = crate::ps2_aux::facts().last;
    ptr.move_by(m.dx, m.dy, fb);
    let (x1, y1) = ptr.position();
    let (probe_x, probe_y) = ptr.probe_point();
    let synthetic_ok = x1 - x0 == 150
        && y0 - y1 == 90
        && fb.get(probe_x, probe_y) == Some(FILL)
        && fb.get(x0 as u32 + 3, y0 as u32 + 4) != Some(FILL);
    crate::serial_println!(
        "  cursor: pointer drawn at ({},{}), a packet of (+150,+90) moved it to ({},{}), fill pixel at ({},{}) reads {} -- {}",
        x0, y0, x1, y1, probe_x, probe_y,
        if synthetic_ok { "white" } else { "not the pointer's fill" },
        if synthetic_ok { "OK" } else { "FAILED" }
    );

    // Then the same path from actual IRQ12 bytes. A headless run has no
    // motion to apply; the line says so rather than implying a test.
    let mut real_moves = 0u32;
    let mut seen_packets = crate::ps2_aux::packets();
    let listen_start = crate::idt::timer_ticks();
    while crate::idt::timer_ticks() - listen_start < 36 {
        // ~2 s at the PIT's 18.2 Hz: enough for a host-driven pointer to
        // move, short enough not to delay a boot gate.
        let now = crate::ps2_aux::packets();
        if now != seen_packets {
            seen_packets = now;
            let real = crate::ps2_aux::facts().last;
            if real.dx != 0 || real.dy != 0 {
                ptr.move_by(real.dx, real.dy, fb);
                real_moves += 1;
            }
        }
        x86_64::instructions::hlt();
    }
    let (px, py) = ptr.position();
    crate::serial_println!(
        "  cursor: {} host-driven motion packet(s) applied in a 2 s listen window; pointer now at ({},{}); {}",
        real_moves, px, py,
        if synthetic_ok { "synthetic path verified" } else { "synthetic path FAILED" }
    );
    synthetic_ok
}
