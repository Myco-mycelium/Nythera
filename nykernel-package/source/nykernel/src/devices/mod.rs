//! Hardware probe and driver autoload.
//!
//! What this is: on every boot the kernel asks the machine what it has
//! (the loader's framebuffer tag, the i8042 controller, the ATA
//! identify, a PCI bus scan) and binds the drivers this kernel actually
//! ships against that answer. What it is NOT: a claim that every
//! controller found is driven. A controller with no driver is reported
//! as `unbound` with the reason, because a limit that is silent is the
//! one that gets shipped.
//!
//! The registry is data (a table of names and requirements), the probe
//! is data (one struct), and binding is a small function per driver --
//! so adding a driver is a row plus its init, not a new `if` in the
//! boot path. The probe order and the bind order are fixed and printed.
//!
//! Ported pieces carry their origin in the module: `framebuffer` (and
//! the Multiboot2 tag 8 decoder it needs) and `pci` come from release
//! 1-71; `ps2-keyboard` and `ata-pio` are this kernel's existing
//! drivers, now registered so the same transcript says whether they are
//! on this machine.

pub mod framebuffer;
pub mod pci;

use x86_64::structures::paging::OffsetPageTable;

use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::multiboot::{FramebufferInfo, MultibootInfo};
use crate::serial_println;

#[derive(Debug, Clone, Copy)]
pub struct Hardware {
    pub fb: Option<FramebufferInfo>,
    pub ps2: bool,
    /// ATA channel index reported by `ata::detect()`, if a drive answered.
    pub ata: Option<usize>,
    pub pci: pci::Inventory,
}

/// Reads the 8042 status register. 0xFF is the documented "no controller
/// on this bus" answer (a floating bus reads all-ones), which is the only
/// presence test that cannot be fooled by a controller with nothing
/// plugged in.
fn ps2_present() -> bool {
    use x86_64::instructions::port::Port;
    let mut status: Port<u8> = Port::new(0x64);
    unsafe { status.read() != 0xFF }
}

/// The one probe pass. Cheap and side-effect free apart from the ATA
/// identify (`ata::detect()` is read-only: IDENTIFY, no writes).
pub fn probe(multiboot: &MultibootInfo) -> Hardware {
    Hardware {
        fb: multiboot.framebuffer(),
        ps2: ps2_present(),
        ata: crate::ata::detect(),
        pci: pci::scan(),
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Bind {
    Bound,
    Absent,
    Unbound,
    Refused,
}

impl Bind {
    pub fn as_str(self) -> &'static str {
        match self {
            Bind::Bound => "bound",
            Bind::Absent => "absent",
            Bind::Unbound => "unbound",
            Bind::Refused => "refused",
        }
    }
}

pub struct BindRecord {
    pub driver: &'static str,
    pub class: &'static str,
    pub bind: Bind,
    pub detail: &'static str,
}

pub struct Devices {
    pub hardware: Hardware,
    pub framebuffer: Option<framebuffer::Framebuffer>,
    /// The PS/2 aux driver's measured facts, when a device answered on
    /// IRQ12. `None` means no mouse answered -- which is a different
    /// fact from "this machine has no 8042", and the transcript says
    /// which one it is.
    pub aux: Option<crate::ps2_aux::Facts>,
    pub binds: alloc::vec::Vec<BindRecord>,
    /// The read-back result of the display self-test: `Some(true)` when
    /// the mapped framebuffer returned the pixels just written.
    pub display_verified: Option<bool>,
}

impl Devices {
    pub fn bound(&self, driver: &str) -> bool {
        self.binds
            .iter()
            .any(|b| b.driver == driver && b.bind == Bind::Bound)
    }
}

/// Binds every driver in the registry against the probe result, in a
/// fixed order: bus first (so its inventory is available), then input,
/// then display, then storage. Returns the records plus the live
/// framebuffer handle when one could be mapped.
pub fn autoload(
    hardware: Hardware,
    mapper: &mut OffsetPageTable<'static>,
    fa: &mut BumpFrameAllocator,
) -> Devices {
    let mut binds = alloc::vec::Vec::new();

    // 1. PCI bus: always applicable on x86_64; it is the inventory the
    //    other USB-class rows are decided from.
    binds.push(BindRecord {
        driver: "pci-bus",
        class: "bus",
        bind: Bind::Bound,
        detail: "legacy config space (0xCF8/0xCFC) enumerated",
    });

    // 2. PS/2 keyboard: this kernel's own IRQ1 driver, already armed by
    //    the input path; the registry row reports whether the machine
    //    has the controller at all.
    binds.push(BindRecord {
        driver: "ps2-keyboard",
        class: "input",
        bind: if hardware.ps2 { Bind::Bound } else { Bind::Absent },
        detail: if hardware.ps2 {
            "i8042 present, IRQ1 handler installed and unmasked"
        } else {
            "no i8042 status register (0xFF): this machine has no PS/2 keyboard"
        },
    });

    // 3. PS/2 aux (mouse): release 1-71's `input.rs` driver, ported as
    //    `crate::ps2_aux`. Binding it is a real device operation (enable
    //    the port, take IRQ12, ask the device for its ID), so it happens
    //    here and not in the side-effect-free probe. Three outcomes are
    //    kept distinct: no controller, controller but no device
    //    answering, and bound with the facts it reported.
    let aux_bind = if hardware.ps2 {
        unsafe { crate::ps2_aux::init() }
    } else {
        crate::ps2_aux::Bind::Absent
    };
    let (aux_bind_kind, aux, aux_detail) = match aux_bind {
        crate::ps2_aux::Bind::Bound(f) => (
            Bind::Bound,
            Some(f),
            "i8042 aux enabled, IRQ12 unmasked (with the cascade), device answered a 0xF2 request",
        ),
        crate::ps2_aux::Bind::PolledOnly(f) => (
            Bind::Refused,
            None,
            "the device answers a polled request but nothing arrived on IRQ12 -- the interrupt path is broken, not the hardware",
        ),
        crate::ps2_aux::Bind::Absent => (
            if hardware.ps2 { Bind::Absent } else { Bind::Absent },
            None,
            if hardware.ps2 {
                "i8042 aux enabled but neither IRQ12 nor a poll got a reply -- no mouse on this machine"
            } else {
                "no PS/2 controller"
            },
        ),
    };
    binds.push(BindRecord {
        driver: "ps2-aux",
        class: "input",
        bind: aux_bind_kind,
        detail: aux_detail,
    });

    // 4. USB HID: report the controller class, never a driver we do not
    //    have. This is the row that keeps "USB found" from reading as
    //    "USB works".
    binds.push(BindRecord {
        driver: "usb-hid",
        class: "input",
        bind: if hardware.pci.usb != 0 { Bind::Unbound } else { Bind::Absent },
        detail: if hardware.pci.usb != 0 {
            "host controller present; HID driver is in the device port (release 1-71 usb.rs/xhci.rs)"
        } else {
            "no USB host controller found on the PCI bus"
        },
    });

    // 5. Framebuffer: bound only when the loader granted a mode this
    //    driver can draw into (direct RGB, 32bpp). Anything else is a
    //    named refusal, not a guessed pixel format.
    let mut framebuffer = None;
    let (bind, detail) = match hardware.fb {
        None => (Bind::Absent, "loader provided no framebuffer tag (no graphics mode requested or granted)"),
        Some(info) if info.kind != 1 || info.bpp != 32 => (
            Bind::Refused,
            "loader granted a non-direct-RGB or non-32bpp mode; this driver does not guess pixel formats",
        ),
        Some(info) => match unsafe { framebuffer::init(info, mapper, fa) } {
            Ok(fb) => {
                framebuffer = Some(fb);
                (Bind::Bound, "linear framebuffer mapped uncached, non-executable")
            }
            Err(framebuffer::FbError::AddressTooLow) => (
                Bind::Refused,
                "framebuffer address is inside the kernel's static identity map",
            ),
            Err(_) => (Bind::Refused, "framebuffer mapping failed"),
        },
    };
    binds.push(BindRecord {
        driver: "framebuffer",
        class: "display",
        bind,
        detail,
    });

    // 6. ATA PIO: this kernel's existing driver, reported per machine.
    binds.push(BindRecord {
        driver: "ata-pio",
        class: "storage",
        bind: if hardware.ata.is_some() { Bind::Bound } else { Bind::Absent },
        detail: if hardware.ata.is_some() {
            "IDENTIFY answered on the primary channel"
        } else {
            "no ATA drive answered IDENTIFY"
        },
    });

    Devices {
        hardware,
        framebuffer,
        aux,
        binds,
        display_verified: None,
    }
}

/// Prints the autoload transcript. Every line names the driver, the
/// class, and what happened on THIS machine, so a boot log is a hardware
/// report rather than a list of code that exists.
pub fn report(devices: &Devices) {
    let bound = devices.binds.iter().filter(|b| b.bind == Bind::Bound).count();
    let unbound = devices.binds.iter().filter(|b| b.bind == Bind::Unbound).count();
    serial_println!(
        "  devices: autoload -- {} driver(s) in the registry, {} bound, {} unbound, {} absent, {} refused",
        devices.binds.len(),
        bound,
        unbound,
        devices.binds.iter().filter(|b| b.bind == Bind::Absent).count(),
        devices.binds.iter().filter(|b| b.bind == Bind::Refused).count()
    );
    for b in &devices.binds {
        serial_println!(
            "  devices:   {:<14} {:<8} {:<8} {}",
            b.driver,
            b.class,
            b.bind.as_str(),
            b.detail
        );
    }
    let pci = devices.hardware.pci;
    serial_println!(
        "  devices: pci -- {} function(s): {} storage, {} display, usb {}",
        pci.functions,
        pci.storage,
        pci.display,
        pci.usb_names()
    );
    if let Some(aux) = &devices.aux {
        if aux.device_id < 0 {
            serial_println!(
                "  devices: ps2-aux -- device id not reported, {} byte(s) on IRQ12, {} packet(s), last motion ({}, {}) buttons {:#05b}",
                aux.bytes,
                aux.packets,
                aux.last.dx,
                aux.last.dy,
                aux.last.buttons
            );
        } else {
            serial_println!(
                "  devices: ps2-aux -- device id {:#04x}, {} byte(s) on IRQ12, {} packet(s), last motion ({}, {}) buttons {:#05b}",
                aux.device_id,
                aux.bytes,
                aux.packets,
                aux.last.dx,
                aux.last.dy,
                aux.last.buttons
            );
        }
    }
    if let Some(fb) = &devices.framebuffer {
        serial_println!(
            "  devices: display -- {}x{} 32bpp {}, read-back {}",
            fb.width,
            fb.height,
            if fb.rgb_first() { "RGBX" } else { "BGRX" },
            match devices.display_verified {
                Some(true) => "verified",
                Some(false) => "MISMATCH",
                None => "not probed",
            }
        );
    }
}
