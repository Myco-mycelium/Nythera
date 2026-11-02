//! 1-67: a minimal xHCI (USB 3 host controller) HID pointer driver -- the controller type of nearly every real PC and of QEMU's `-device qemu-xhci`. IN THE KERNEL (RC-28).
//! Polled, one device on a root-hub port, no hubs, no hot-plug, no interrupts, no streams, no legacy-BIOS handoff. Command ring + event ring + one transfer ring for the
//! default control pipe and one for the HID interrupt-IN endpoint; the report goes through `usb::deliver` (same PS/2-byte conversion as the UHCI driver).
//! All DMA memory is one kernel .bss pool (physical = virtual); the controller's registers (BAR0, normally above 1 GiB) are mapped by `map_mmio` at boot.
use crate::experimental::usb::{delay_ms, deliver};
use core::ptr::{read_volatile as rv, write_volatile as wv};
use x86_64::structures::paging::{Mapper, OffsetPageTable, Page, PageTableFlags, PhysFrame, Size4KiB};
use x86_64::{PhysAddr, VirtAddr};

#[repr(C, align(4096))] struct Pool([u8; 0x20000]);
static mut POOL: Pool = Pool([0; 0x20000]);
const DCBAA: u64 = 0; const CMDR: u64 = 0x1000; const EVTR: u64 = 0x2000; const ERST: u64 = 0x3000; const INCTX: u64 = 0x4000; const OUTCTX: u64 = 0x5000;
const EP0R: u64 = 0x6000; const INTR: u64 = 0x7000; const DBUF: u64 = 0x8000; const RBUF: u64 = 0x8200; const SPA: u64 = 0x9000; const SPP: u64 = 0xA000;
const RN: usize = 64;                                         // TRBs per ring (the last one is the link TRB)

static mut BASE: u64 = 0; static mut OP: u64 = 0; static mut DB: u64 = 0; static mut RT: u64 = 0; static mut CS: u64 = 32;
static mut CMD: Ring = Ring { base: 0, idx: 0, pcs: 1 }; static mut EP0: Ring = Ring { base: 0, idx: 0, pcs: 1 }; static mut INT: Ring = Ring { base: 0, idx: 0, pcs: 1 };
static mut EIDX: usize = 0; static mut ECC: u32 = 1;
static mut SLOT: u32 = 0; static mut DCI: u32 = 0; static mut ACTIVE: bool = false; static mut TABLET: bool = false; static mut RLEN: u32 = 4;
struct Ring { base: u64, idx: usize, pcs: u32 }

unsafe fn pool(off: u64) -> u64 { core::ptr::addr_of_mut!(POOL) as u64 + off }
unsafe fn r32(a: u64) -> u32 { rv(a as *const u32) }
unsafe fn w32(a: u64, v: u32) { wv(a as *mut u32, v) }
unsafe fn w64(a: u64, v: u64) { w32(a, v as u32); w32(a + 4, (v >> 32) as u32) }

/// Boot-time (needs the page mapper): find the controller and map its register block (64 KiB) when it lives above the identity-mapped first GiB.
pub fn map_mmio(mapper: &mut OffsetPageTable<'static>, fa: &mut crate::memory::frame_allocator::BumpFrameAllocator) {
    let Some(b) = crate::experimental::pci::find_xhci() else { return };
    unsafe { BASE = b; }
    if b < 0x4000_0000 { return; }
    let flags = PageTableFlags::PRESENT | PageTableFlags::WRITABLE | PageTableFlags::NO_EXECUTE | PageTableFlags::NO_CACHE | PageTableFlags::WRITE_THROUGH;
    for i in 0..16u64 {
        let phys = PhysFrame::<Size4KiB>::containing_address(PhysAddr::new(b + i * 4096));
        let page = Page::<Size4KiB>::containing_address(VirtAddr::new(b + i * 4096));
        if let Ok(f) = unsafe { mapper.map_to(page, phys, flags, fa) } { f.flush(); }
    }
}

unsafe fn enq(r: &mut Ring, d0: u32, d1: u32, d2: u32, d3: u32) {
    let p = (r.base + 16 * r.idx as u64) as *mut u32;
    wv(p, d0); wv(p.add(1), d1); wv(p.add(2), d2); core::sync::atomic::fence(core::sync::atomic::Ordering::SeqCst); wv(p.add(3), d3 | r.pcs);
    r.idx += 1;
    if r.idx == RN - 1 {
        let l = (r.base + 16 * (RN as u64 - 1)) as *mut u32;
        wv(l, r.base as u32); wv(l.add(1), (r.base >> 32) as u32); wv(l.add(2), 0); wv(l.add(3), (6 << 10) | 2 | r.pcs);   // link back to the start, toggle cycle
        r.idx = 0; r.pcs ^= 1;
    }
}
unsafe fn next_event(wait_ms: u32) -> Option<[u32; 4]> {
    for _ in 0..=wait_ms {
        let p = (pool(EVTR) + 16 * EIDX as u64) as *const u32;
        if rv(p.add(3)) & 1 == ECC {
            let ev = [rv(p), rv(p.add(1)), rv(p.add(2)), rv(p.add(3))];
            EIDX += 1; if EIDX == RN { EIDX = 0; ECC ^= 1; }
            w64(RT + 0x38, (pool(EVTR) + 16 * EIDX as u64) | 8);                     // ERDP, EHB cleared
            return Some(ev);
        }
        delay_ms(1);
    }
    None
}
unsafe fn command(d0: u32, d1: u32, d3: u32) -> Option<(u32, u32)> {
    enq(&mut CMD, d0, d1, 0, d3); w32(DB, 0);
    for _ in 0..2000 {
        let Some(ev) = next_event(1) else { continue };
        if (ev[3] >> 10) & 63 == 33 { return Some((ev[2] >> 24, ev[3] >> 24)); }       // command completion: (code, slot id)
    }
    None
}
unsafe fn ctx(base: u64, i: u64) -> *mut u32 { (base + i * CS) as *mut u32 }
unsafe fn ctrl(req: [u8; 8], len: usize, dir_in: bool) -> bool {
    let d0 = u32::from_le_bytes([req[0], req[1], req[2], req[3]]); let d1 = u32::from_le_bytes([req[4], req[5], req[6], req[7]]);
    let trt = if len == 0 { 0 } else if dir_in { 3 } else { 2 };
    enq(&mut EP0, d0, d1, 8, (2 << 10) | (1 << 6) | (trt << 16));
    if len > 0 { enq(&mut EP0, pool(DBUF) as u32, (pool(DBUF) >> 32) as u32, len as u32, (3 << 10) | ((dir_in as u32) << 16)); }
    enq(&mut EP0, 0, 0, 0, (4 << 10) | (1 << 5) | ((!(len > 0 && dir_in) as u32) << 16));
    w32(DB + 4 * SLOT as u64, 1);
    for _ in 0..2000 {
        let Some(ev) = next_event(1) else { continue };
        if (ev[3] >> 10) & 63 == 32 { let cc = ev[2] >> 24; return cc == 1 || cc == 13; }
    }
    false
}
unsafe fn zero(a: u64, n: usize) { for i in 0..n { wv((a + i as u64) as *mut u8, 0); } }

/// LIVE session only. 0 = no xHCI / no HID device, 1 = boot mouse, 2 = tablet.
pub fn init() -> u8 {
    unsafe {
        if BASE == 0 { return 0; }
        let cap = BASE; let caplen = rv(cap as *const u8) as u64; OP = cap + caplen; DB = cap + r32(cap + 0x14) as u64; RT = cap + r32(cap + 0x18) as u64;
        let hcs1 = r32(cap + 4); let hcs2 = r32(cap + 8); let hcc1 = r32(cap + 0x10);
        let (maxslots, maxports) = ((hcs1 & 255).min(8), (hcs1 >> 24) as u64); CS = if hcc1 & 4 != 0 { 64 } else { 32 };
        // stop, reset
        w32(OP, r32(OP) & !1); for _ in 0..200 { if r32(OP + 4) & 1 != 0 { break; } delay_ms(1); }
        w32(OP, r32(OP) | 2); for _ in 0..500 { if r32(OP) & 2 == 0 && r32(OP + 4) & (1 << 11) == 0 { break; } delay_ms(1); }
        if r32(OP + 4) & (1 << 11) != 0 { crate::serial_println!("  xhci: controller not ready after reset"); return 0; }
        w32(OP + 0x38, maxslots);
        // scratchpad buffers
        let nsp = (((hcs2 >> 27) & 31) | (((hcs2 >> 21) & 31) << 5)) as u64;
        if nsp > 0 { if nsp > 16 { crate::serial_println!("  xhci: {} scratchpad pages needed (max 16)", nsp); return 0; }
            for i in 0..nsp { wv((pool(SPA) + 8 * i) as *mut u64, pool(SPP) + 0x1000 * i); }
            wv(pool(DCBAA) as *mut u64, pool(SPA)); }
        w64(OP + 0x30, pool(DCBAA));
        CMD = Ring { base: pool(CMDR), idx: 0, pcs: 1 }; EP0 = Ring { base: pool(EP0R), idx: 0, pcs: 1 }; INT = Ring { base: pool(INTR), idx: 0, pcs: 1 };
        w64(OP + 0x18, pool(CMDR) | 1);
        wv(pool(ERST) as *mut u64, pool(EVTR)); wv((pool(ERST) + 8) as *mut u32, RN as u32);
        w32(RT + 0x28, 1); w64(RT + 0x38, pool(EVTR)); w64(RT + 0x30, pool(ERST)); EIDX = 0; ECC = 1;
        w32(OP, r32(OP) | 1); for _ in 0..200 { if r32(OP + 4) & 1 == 0 { break; } delay_ms(1); }
        delay_ms(20);
        for port in 1..=maxports {
            let po = OP + 0x400 + 0x10 * (port - 1); let keep = !((1u32 << 1) | (0x7F << 17) | (1 << 4));      // keep PP etc.; never write 1 to PED / change bits by accident
            if r32(po) & 1 == 0 { continue; }
            w32(po, (r32(po) & keep) | (1 << 4));                                                               // port reset
            let mut ok = false; for _ in 0..200 { if r32(po) & (1 << 21) != 0 { ok = true; break; } delay_ms(1); }
            w32(po, (r32(po) & keep) | (1 << 21) | (1 << 17));                                                  // clear the change bits
            if !ok || r32(po) & 2 == 0 { crate::serial_println!("  xhci: port {} did not enable", port); continue; }
            let speed = (r32(po) >> 10) & 15;
            let Some((cc, slot)) = command(0, 0, 9 << 10) else { crate::serial_println!("  xhci: enable-slot timed out"); return 0 };
            if cc != 1 { crate::serial_println!("  xhci: enable-slot code {}", cc); continue; }
            SLOT = slot;
            zero(pool(OUTCTX), 33 * CS as usize); zero(pool(INCTX), 33 * CS as usize);
            wv((pool(DCBAA) + 8 * slot as u64) as *mut u64, pool(OUTCTX));
            let mps: u32 = match speed { 2 => 8, 1 | 3 => 64, _ => 512 };
            wv(ctx(pool(INCTX), 0).add(1), 3);                                                                  // add: slot + EP0
            wv(ctx(pool(INCTX), 1), (speed << 20) | (1 << 27)); wv(ctx(pool(INCTX), 1).add(1), (port as u32) << 16);
            wv(ctx(pool(INCTX), 2).add(1), (3 << 1) | (4 << 3) | (mps << 16));
            wv(ctx(pool(INCTX), 2).add(2), (pool(EP0R) as u32) | 1); wv(ctx(pool(INCTX), 2).add(3), (pool(EP0R) >> 32) as u32); wv(ctx(pool(INCTX), 2).add(4), 8);
            let Some((cc, _)) = command(pool(INCTX) as u32, (pool(INCTX) >> 32) as u32, (11 << 10) | (slot << 24)) else { crate::serial_println!("  xhci: address-device timed out"); return 0 };
            if cc != 1 { crate::serial_println!("  xhci: address-device code {} (speed {})", cc, speed); continue; }
            if !ctrl([0x80, 0x06, 0, 2, 0, 0, 9, 0], 9, true) { crate::serial_println!("  xhci: config descriptor read failed"); continue; }
            let d = pool(DBUF) as *const u8;
            let tot = (((*d.add(3) as usize) << 8) | *d.add(2) as usize).min(128); let cfgv = *d.add(5);
            if !ctrl([0x80, 0x06, 0, 2, 0, 0, tot as u8, 0], tot, true) { crate::serial_println!("  xhci: full config descriptor read failed"); continue; }
            let (mut proto, mut ep, mut epmps, mut inhid) = (0u8, 0u8, 0u32, false); let mut i = 0usize;
            while i + 2 <= tot && *d.add(i) >= 2 {
                let (l, t) = (*d.add(i) as usize, *d.add(i + 1));
                if t == 4 { inhid = *d.add(i + 5) == 3; if inhid && ep == 0 { proto = *d.add(i + 7); } }
                else if t == 5 && inhid && ep == 0 && *d.add(i + 2) & 0x80 != 0 && *d.add(i + 3) & 3 == 3 { ep = *d.add(i + 2) & 15; epmps = *d.add(i + 4) as u32 | ((*d.add(i + 5) as u32) << 8); }
                i += l;
            }
            if ep == 0 { crate::serial_println!("  xhci: port {}: no HID interrupt-IN endpoint", port); continue; }
            DCI = ep as u32 * 2 + 1; TABLET = proto == 0; RLEN = if TABLET { 6 } else { 4 }; if RLEN > (epmps & 0x7FF) { RLEN = epmps & 0x7FF; }
            // configure endpoint: slot context copied from the output context, entries raised, the interrupt endpoint added
            zero(pool(INCTX), 33 * CS as usize);
            for k in 0..8 { wv(ctx(pool(INCTX), 1).add(k), rv(ctx(pool(OUTCTX), 0).add(k))); }
            wv(ctx(pool(INCTX), 0).add(1), 1 | (1 << DCI));
            let s0 = rv(ctx(pool(INCTX), 1)); wv(ctx(pool(INCTX), 1), (s0 & !(31 << 27)) | (DCI << 27));
            let e = ctx(pool(INCTX), 1 + DCI as u64);
            wv(e, (if speed == 3 { 5u32 } else { 6 }) << 16); wv(e.add(1), (3 << 1) | (7 << 3) | ((epmps & 0x7FF) << 16));
            wv(e.add(2), (pool(INTR) as u32) | 1); wv(e.add(3), (pool(INTR) >> 32) as u32); wv(e.add(4), (epmps & 0x7FF) | ((epmps & 0x7FF) << 16));
            let Some((cc, _)) = command(pool(INCTX) as u32, (pool(INCTX) >> 32) as u32, (12 << 10) | (slot << 24)) else { crate::serial_println!("  xhci: configure-endpoint timed out"); return 0 };
            if cc != 1 { crate::serial_println!("  xhci: configure-endpoint code {}", cc); continue; }
            if !ctrl([0x00, 0x09, cfgv, 0, 0, 0, 0, 0], 0, false) { crate::serial_println!("  xhci: set-configuration failed"); continue; }
            queue_report(); ACTIVE = true;
            crate::serial_println!("  usb: xHCI port {} speed {} HID endpoint {} report {} bytes -> {}", port, speed, ep, RLEN, if TABLET { "absolute tablet" } else { "boot mouse" });
            return if TABLET { 2 } else { 1 };
        }
        0
    }
}
unsafe fn queue_report() {
    enq(&mut INT, pool(RBUF) as u32, (pool(RBUF) >> 32) as u32, RLEN, (1 << 10) | (1 << 5) | (1 << 2));       // normal TRB, IOC + interrupt on short packet
    w32(DB + 4 * SLOT as u64, DCI);
}
/// Called from INPUT_POLL (via usb::poll): reap transfer events, deliver the report, queue the next one.
pub fn poll() {
    unsafe {
        if !ACTIVE { return; }
        while let Some(ev) = next_event(0) {
            if (ev[3] >> 10) & 63 != 32 { continue; }
            let cc = ev[2] >> 24;
            if cc == 1 || cc == 13 { deliver(pool(RBUF) as *const u8, TABLET); }
            queue_report();
        }
    }
}
