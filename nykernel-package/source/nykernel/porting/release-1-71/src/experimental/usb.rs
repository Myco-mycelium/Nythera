//! 1-66: a minimal USB HID pointer driver for UHCI (USB 1.1) host controllers -- what QEMU's `-usb` provides. IN THE KERNEL, like the PS/2 setup (RC-17/RC-27).
//! Scope: one HID device on a root port, found and configured by assuming the usual shape (default control pipe, 8-byte packets; the configuration descriptor is parsed only
//! for the first HID interface's interrupt-IN endpoint). Two report shapes: a BOOT MOUSE (protocol 2: buttons, dx, dy, wheel) and an absolute TABLET (protocol 0, as QEMU's
//! usb-tablet: buttons, x u16, y u16, wheel; x/y 0..32767). Both are turned into the PS/2 byte stream the existing router/decoder already understand (a tablet becomes the relative
//! moves that bring the router's pointer to the reported place), so nothing above the kernel changes. No hubs, no hot-plug, no interrupts: the controller is polled from INPUT_POLL.
//! Memory for the frame list / queue heads / transfer descriptors is kernel .bss, identity-mapped (physical = virtual), 4 KiB aligned where required.
use x86_64::instructions::port::Port;

#[repr(C, align(4096))] struct Frames([u32; 1024]);
#[repr(C, align(16))] #[derive(Clone, Copy)] struct Td { link: u32, cs: u32, token: u32, buf: u32 }
#[repr(C, align(16))] struct Qh { head: u32, elem: u32, pad: [u32; 2] }
static mut FRAMES: Frames = Frames([1; 1024]);
static mut QH_CTRL: Qh = Qh { head: 1, elem: 1, pad: [0; 2] };
static mut QH_INT: Qh = Qh { head: 1, elem: 1, pad: [0; 2] };
static mut TDS: [Td; 12] = [Td { link: 1, cs: 0, token: 0, buf: 0 }; 12];
static mut INT_TD: Td = Td { link: 1, cs: 0, token: 0, buf: 0 };
#[repr(C, align(16))] struct Buf([u8; 64]);
static mut DBUF: Buf = Buf([0; 64]);          // control-transfer data stage
static mut SETUP: Buf = Buf([0; 64]);
static mut RBUF: Buf = Buf([0; 64]);          // interrupt report
static mut IO: u16 = 0;
static mut LS: bool = false;                   // low-speed device
static mut ADDR: u32 = 0;
static mut EP: u32 = 1; static mut RLEN: u32 = 4; static mut TABLET: bool = false; static mut TOGGLE: u32 = 0;
static mut ACTIVE: bool = false;
static mut CX: i32 = 512; static mut CY: i32 = 384; static mut BTN: u8 = 0; static mut SEEN: bool = false;

pub fn delay_ms(ms: u32) { let mut p: Port<u8> = Port::new(0x80); for _ in 0..ms * 1000 { unsafe { p.write(0) }; } }
unsafe fn r16(off: u16) -> u16 { Port::<u16>::new(IO + off).read() }
unsafe fn w16(off: u16, v: u16) { Port::<u16>::new(IO + off).write(v) }
unsafe fn w32(off: u16, v: u32) { Port::<u32>::new(IO + off).write(v) }
unsafe fn wb(off: u16, v: u8) { Port::<u8>::new(IO + off).write(v) }
fn rd(p: *const u32) -> u32 { unsafe { core::ptr::read_volatile(p) } }
fn wr(p: *mut u32, v: u32) { unsafe { core::ptr::write_volatile(p, v) } }

unsafe fn td_set(i: usize, last: bool, pid: u32, toggle: u32, len: u32, buf: u32) {
    let next = if last { 1 } else { (core::ptr::addr_of!(TDS[i + 1]) as u32) | 4 };
    let ls = if LS { 1 << 26 } else { 0 };
    wr(core::ptr::addr_of_mut!(TDS[i].link), next);
    wr(core::ptr::addr_of_mut!(TDS[i].buf), buf);
    wr(core::ptr::addr_of_mut!(TDS[i].token), pid | (ADDR << 8) | (toggle << 19) | ((len.wrapping_sub(1) & 0x7FF) << 21));
    wr(core::ptr::addr_of_mut!(TDS[i].cs), (3 << 27) | ls | (1 << 23));     // active, 3 errors allowed
}
/// One control transfer on the default pipe of device ADDR (8-byte packets). `dir_in`: data stage direction; `len` bytes into/from DBUF. Returns true on success.
unsafe fn control(req: [u8; 8], len: usize, dir_in: bool) -> bool {
    let sp = core::ptr::addr_of_mut!(SETUP.0) as *mut u8; for i in 0..8 { *sp.add(i) = req[i]; }
    let db = core::ptr::addr_of_mut!(DBUF.0) as u32;
    let mut n = 0usize;
    td_set(n, false, 0x2D, 0, 8, sp as u32); n += 1;
    let mut off = 0usize; let mut tog = 1u32;
    while off < len { let c = (len - off).min(8); td_set(n, false, if dir_in { 0x69 } else { 0xE1 }, tog, c as u32, db + off as u32); n += 1; off += c; tog ^= 1; }
    td_set(n, true, if dir_in && len > 0 { 0xE1 } else { 0x69 }, 1, 0, 0);       // status stage: opposite direction, DATA1, zero length (the length field encodes 0 as 0x7FF)
    wr(core::ptr::addr_of_mut!(TDS[n].token), (rd(core::ptr::addr_of!(TDS[n].token)) & !(0x7FF << 21)) | (0x7FF << 21));
    wr(core::ptr::addr_of_mut!(QH_CTRL.elem), core::ptr::addr_of!(TDS[0]) as u32);
    let mut spins = 0u32;
    loop {
        if rd(core::ptr::addr_of!(TDS[n].cs)) & (1 << 23) == 0 { break; }
        let mut bad = false; for k in 0..=n { let cs = rd(core::ptr::addr_of!(TDS[k].cs)); if cs & (1 << 23) == 0 && cs & 0x760000 != 0 { bad = true; } }
        if bad { wr(core::ptr::addr_of_mut!(QH_CTRL.elem), 1); return false; }
        spins += 1; if spins > 2000 { wr(core::ptr::addr_of_mut!(QH_CTRL.elem), 1); return false; }
        delay_ms(1);
    }
    wr(core::ptr::addr_of_mut!(QH_CTRL.elem), 1);
    for k in 0..=n { if rd(core::ptr::addr_of!(TDS[k].cs)) & 0x760000 != 0 { return false; } }
    true
}
unsafe fn port_reset(po: u16) -> bool {
    w16(po, 1 << 9); delay_ms(60); w16(po, 0); delay_ms(10);
    for _ in 0..10 { w16(po, (1 << 2) | 0x0A); delay_ms(10); if r16(po) & 4 != 0 { return true; } }       // enable the port (and clear the change bits)
    false
}
/// LIVE session only. Returns 0 = no UHCI / no HID device, 1 = boot mouse active, 2 = tablet active.
pub fn init() -> u8 {
    unsafe {
        let Some((_, _, _, io)) = crate::experimental::pci::find_uhci() else { return crate::experimental::xhci::init() };
        IO = io;
        w16(0, 4); delay_ms(20); w16(0, 0); w16(0, 2); delay_ms(20); w16(0, 0);          // global reset, then host-controller reset
        for i in 0..1024 { FRAMES.0[i] = (core::ptr::addr_of!(QH_CTRL) as u32) | 2; }
        wr(core::ptr::addr_of_mut!(QH_CTRL.head), (core::ptr::addr_of!(QH_INT) as u32) | 2); wr(core::ptr::addr_of_mut!(QH_CTRL.elem), 1);
        wr(core::ptr::addr_of_mut!(QH_INT.head), 1); wr(core::ptr::addr_of_mut!(QH_INT.elem), 1);
        w16(6, 0); w32(8, core::ptr::addr_of!(FRAMES) as u32); wb(0x0C, 0x40); w16(2, 0x3F); w16(4, 0);
        w16(0, 0x81);                                                                      // run, 64-byte max packet
        delay_ms(20);
        for po in [0x10u16, 0x12] {
            if r16(po) & 1 == 0 { continue; }
            LS = r16(po) & 0x100 != 0;
            if !port_reset(po) { continue; }
            ADDR = 0;
            if !control([0x00, 0x05, 1, 0, 0, 0, 0, 0], 0, false) { continue; }             // SET_ADDRESS(1)
            ADDR = 1; delay_ms(5);
            if !control([0x80, 0x06, 0, 2, 0, 0, 9, 0], 9, true) { continue; }              // GET_DESCRIPTOR(configuration), first 9 bytes
            let tot = (((DBUF.0[3] as usize) << 8) | DBUF.0[2] as usize).min(64);
            let cfgv = DBUF.0[5];
            if !control([0x80, 0x06, 0, 2, 0, 0, tot as u8, 0], tot, true) { continue; }
            let (mut proto, mut ep, mut mps, mut inhid) = (0u8, 0u8, 0u8, false); let mut i = 0usize;
            while i + 2 <= tot && DBUF.0[i] >= 2 {
                let (l, t) = (DBUF.0[i] as usize, DBUF.0[i + 1]);
                if t == 4 { inhid = DBUF.0[i + 5] == 3; if inhid && ep == 0 { proto = DBUF.0[i + 7]; } }
                else if t == 5 && inhid && ep == 0 && DBUF.0[i + 2] & 0x80 != 0 && DBUF.0[i + 3] & 3 == 3 { ep = DBUF.0[i + 2] & 15; mps = DBUF.0[i + 4]; }
                i += l;
            }
            if ep == 0 { continue; }
            if !control([0x00, 0x09, cfgv, 0, 0, 0, 0, 0], 0, false) { continue; }          // SET_CONFIGURATION
            TABLET = proto == 0; EP = ep as u32; RLEN = if TABLET { 6 } else { 4 }; if RLEN > mps as u32 { RLEN = mps as u32; }
            TOGGLE = 0; arm(); ACTIVE = true;
            crate::serial_println!("  usb: UHCI port {} low-speed={} HID endpoint {} report {} bytes -> {}", if po == 0x10 { 1 } else { 2 }, LS as u8, ep, RLEN, if TABLET { "absolute tablet" } else { "boot mouse" });
            return if TABLET { 2 } else { 1 };
        }
        crate::experimental::xhci::init()
    }
}
unsafe fn arm() {
    wr(core::ptr::addr_of_mut!(INT_TD.link), 1); wr(core::ptr::addr_of_mut!(INT_TD.buf), core::ptr::addr_of!(RBUF) as u32);
    wr(core::ptr::addr_of_mut!(INT_TD.token), 0x69 | (ADDR << 8) | (EP << 15) | (TOGGLE << 19) | ((RLEN - 1) << 21));
    wr(core::ptr::addr_of_mut!(INT_TD.cs), (3 << 27) | (if LS { 1 << 26 } else { 0 }) | (1 << 23));
    wr(core::ptr::addr_of_mut!(QH_INT.elem), core::ptr::addr_of!(INT_TD) as u32);
}
fn pkt(b: u8, dx: i32, dy_up: i32) {
    let (dx, dy) = (dx.clamp(-127, 127), dy_up.clamp(-127, 127));
    let st = 0x08 | (b & 7) | (((dx < 0) as u8) << 4) | (((dy < 0) as u8) << 5);
    for x in [st, dx as u8, dy as u8] { crate::experimental::input::push_mouse_byte(x); }
}

/// One HID report (boot mouse: buttons, dx, dy; tablet: buttons, x u16, y u16) -> PS/2 packets for the router. Shared by the UHCI and xHCI drivers.
pub unsafe fn deliver(r: *const u8, tablet: bool) {
    let b = *r & 7;
    if !tablet { pkt(b, *r.add(1) as i8 as i32, -(*r.add(2) as i8 as i32)); }
    else {
        let x = (*r.add(1) as u32 | ((*r.add(2) as u32) << 8)) as i32; let y = (*r.add(3) as u32 | ((*r.add(4) as u32) << 8)) as i32;
        let (tx, ty) = ((x * 1023 + 16383) / 32767, (y * 767 + 16383) / 32767);   // rounded: QEMU scaled the pixel by 32767/(size-1)
        let mut guard = 0;
        while (CX, CY) != (tx, ty) && guard < 16 { let dx = (tx - CX).clamp(-127, 127); let dy = (ty - CY).clamp(-127, 127); pkt(b, dx, -dy); CX += dx; CY += dy; guard += 1; }
        if b != BTN || !SEEN { if (CX, CY) == (tx, ty) { pkt(b, 0, 0); } }
        BTN = b; SEEN = true;
    }
}
/// Called from INPUT_POLL: reap a finished report (if any), turn it into PS/2 packets, re-arm.
pub fn poll() {
    crate::experimental::xhci::poll();
    unsafe {
        if !ACTIVE { return; }
        let cs = rd(core::ptr::addr_of!(INT_TD.cs));
        if cs & (1 << 23) != 0 { return; }
        if cs & 0x760000 == 0 {
            deliver(core::ptr::addr_of!(RBUF.0) as *const u8, TABLET);
            TOGGLE ^= 1;
        }
        arm();
    }
}
