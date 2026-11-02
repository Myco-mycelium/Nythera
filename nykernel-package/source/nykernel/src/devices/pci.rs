//! PCI configuration-space enumeration -- what hardware is actually on
//! this machine, for the driver autoloader.
//!
//! Ported from release 1-71's `src/experimental/pci.rs` (legacy
//! 0xCF8/0xCFC config access), widened from "which USB host controllers
//! exist" to the inventory the loader needs: device count, plus the
//! class codes of the things this kernel could drive or honestly report
//! as unbound. It deliberately does *not* claim a USB or storage driver
//! exists: finding a controller changes which drivers the autoloader
//! reports as `unbound`, which is the point -- a limit that is visible
//! instead of silent.

use x86_64::instructions::port::Port;

fn cfg_read(bus: u32, dev: u32, func: u32, off: u32) -> u32 {
    unsafe {
        let mut a: Port<u32> = Port::new(0xCF8);
        let mut d: Port<u32> = Port::new(0xCFC);
        a.write(0x8000_0000 | (bus << 16) | (dev << 11) | (func << 8) | (off & 0xFC));
        d.read()
    }
}

#[derive(Debug, Clone, Copy, Default)]
pub struct Inventory {
    /// Functions found (not buses/devices: a multi-function device counts
    /// once per function, which is what a driver binds to).
    pub functions: u32,
    /// Class 0x01: mass storage controllers (IDE/SATA/NVMe).
    pub storage: u32,
    /// Class 0x03: display controllers.
    pub display: u32,
    /// Bit mask of USB host-controller kinds: 1 UHCI, 2 OHCI, 4 EHCI,
    /// 8 xHCI (class 0x0C, subclass 0x03, prog-if 0x00/0x10/0x20/0x30).
    pub usb: u8,
}

impl Inventory {
    pub fn usb_names(&self) -> &'static str {
        match self.usb {
            0 => "none",
            1 => "UHCI",
            2 => "OHCI",
            4 => "EHCI",
            8 => "xHCI",
            3 => "UHCI+OHCI",
            9 => "UHCI+xHCI",
            12 => "EHCI+xHCI",
            15 => "UHCI+OHCI+EHCI+xHCI",
            _ => "other",
        }
    }
}

/// Full bus 0..=255 scan. ~65k config reads worst case, all of them on
/// two I/O ports; on QEMU this is milliseconds and on real firmware the
/// missing buses answer 0xFFFF immediately.
pub fn scan() -> Inventory {
    let mut inv = Inventory::default();
    for bus in 0..=255u32 {
        for dev in 0..32u32 {
            if cfg_read(bus, dev, 0, 0) & 0xFFFF == 0xFFFF {
                continue;
            }
            let multi = (cfg_read(bus, dev, 0, 0x0C) >> 16) & 0x80 != 0;
            let funcs = if multi { 8 } else { 1 };
            for func in 0..funcs {
                if cfg_read(bus, dev, func, 0) & 0xFFFF == 0xFFFF {
                    continue;
                }
                inv.functions += 1;
                let cls = cfg_read(bus, dev, func, 0x08);
                // (class << 24) | (subclass << 16) | (prog-if << 8) | revision
                let class = (cls >> 24) & 0xFF;
                if class == 0x01 {
                    inv.storage += 1;
                } else if class == 0x03 {
                    inv.display += 1;
                } else if class == 0x0C && ((cls >> 16) & 0xFF) == 0x03 {
                    inv.usb |= match (cls >> 8) & 0xFF {
                        0x00 => 1,
                        0x10 => 2,
                        0x20 => 4,
                        0x30 => 8,
                        _ => 0,
                    };
                }
            }
        }
    }
    inv
}
