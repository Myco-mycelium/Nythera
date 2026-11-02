#![no_std]
//! `desk`: the AndroWin-style desktop shell as a USER-SPACE process (increments 1-54, 1-55). It holds only the capabilities livesh gives the shell: Endpoint (events), SharedMem/FB (window), Reply.
use core::arch::{asm, global_asm}; use core::panic::PanicInfo; use core::ptr::{addr_of, addr_of_mut};
mod assets; mod gfx; mod scene; mod ev; mod nfs; mod sha; mod nygi;
use gfx::*; use scene::*;
#[panic_handler] fn panic(_: &PanicInfo) -> ! { loop {} }
static mut STACK: [u8; 1 << 17] = [0; 1 << 17];
global_asm!(".section .text._start,\"ax\"", ".global _start", "_start:", "lea rsp, [rip + {st} + 131056]", "and rsp, -16", "call {m}", "ud2", st = sym STACK, m = sym desk_main);
#[inline(always)] pub(crate) unsafe fn sys(n: u64, a: u64, b: u64, c: u64, d: u64, e: u64) -> (i64, u64) {
    let r: i64; let rd: u64;
    asm!("int 0x84", inlateout("rax") n as i64 => r, in("rdi") a, in("rsi") b, inlateout("rdx") c => rd, in("r10") d, in("r8") e, options(nostack));
    (r, rd)
}
static mut MSG: [u64; 8] = [0; 8]; static mut FBI: [u32; 8] = [0; 8];
static mut WALL: Buf = [0; W * H]; static mut BACK: Buf = [0; W * H];
const FBWIN: u64 = 0x40000200000;
unsafe fn present(pitch: usize, x: i32, y: i32, w: i32, h: i32) {
    let fb = FBWIN as *mut u32; let (x0, y0) = (x.max(0) as usize, y.max(0) as usize); let (x1, y1) = ((x + w).min(W as i32) as usize, (y + h).min(H as i32) as usize);
    for py in y0..y1 { core::ptr::copy_nonoverlapping((addr_of!(BACK) as *const u32).add(py * W + x0), fb.add(py * pitch + x0), x1 - x0); }
}
const CUR: [&[u8; 12]; 17] = [b"X...........", b"XX..........", b"X.X.........", b"X..X........", b"X...X.......", b"X....X......", b"X.....X.....", b"X......X....", b"X.......X...", b"X........X..", b"X.....XXXXX.", b"X..X..X.....", b"X.X.X..X....", b"XX..X..X....", b"X....X..X...", b".....X..X...", b"......XX...."];
unsafe fn cursor(pitch: usize, mx: i32, my: i32) {
    let fb = FBWIN as *mut u32;
    for (j, row) in CUR.iter().enumerate() { let mut inside = false; for (i, &c) in row.iter().enumerate() { let (px, py) = (mx + i as i32, my + j as i32); if px >= W as i32 || py >= H as i32 || px < 0 || py < 0 { continue; }
        if c == b'X' { inside = !inside; *fb.add(py as usize * pitch + px as usize) = 0xFF000000; } else if inside { *fb.add(py as usize * pitch + px as usize) = 0xFFFFFFFF; } } }
}
static mut ST: St = new_state();
unsafe fn redraw(pitch: usize) { let b = &mut *addr_of_mut!(BACK); b.copy_from_slice(&*addr_of!(WALL)); clear_clip(); draw(b, &*addr_of!(ST)); present(pitch, 0, 0, W as i32, H as i32); cursor(pitch, ST.mx, ST.my); }
#[no_mangle] pub extern "C" fn desk_main() -> ! { unsafe {
    let (_, cap) = sys(2, 0, MSG.as_mut_ptr() as u64, 64, 1, 0);
    sys(20, cap, FBI.as_mut_ptr() as u64, 0, 0, 0); sys(21, cap, FBWIN, 0, 0, 0);
    let (_, nycap) = sys(2, 0, MSG.as_mut_ptr() as u64, 64, 1, 0); nfs::NY = nycap;                         // 1-60: the SEND capability to the NyFS service ...
    let (_, rcap) = sys(2, 0, MSG.as_mut_ptr() as u64, 64, 1, 0); sys(8, rcap, nfs::REGION, 0, 0, 0);       // ... and the file-data region it shares with that service
    let (_, icap) = sys(2, 0, MSG.as_mut_ptr() as u64, 64, 1, 0); sys(8, icap, nygi::REGION, 0, 0, 0);       // 1-62: the game-image region (livesh copies a .nygi into it; desk verifies it)
    let (_, rep) = sys(2, 0, MSG.as_mut_ptr() as u64, 64, 1, 0);
    let pitch = FBI[2] as usize / 4;
    wallpaper(&mut *addr_of_mut!(WALL)); nfs::probe(); ev::fs_refresh(&mut *addr_of_mut!(ST)); redraw(pitch);
    MSG[0] = 0; sys(10, rep, MSG.as_ptr() as u64, 8, 0, 0);
    let mut lrp = 0u64;                                                                               // 1-60: the launcher's long-poll reply capability (livesh answers a click with a program index)
    loop { let (_, rp) = sys(2, 0, MSG.as_mut_ptr() as u64, 64, 1, 0); let (t, a, b2) = (MSG[0], MSG[1] as i64, MSG[2] as i64);
        if t == 100 { ST.pout = MSG[7]; } let (ox, oy) = (ST.mx, ST.my); let (redraw_now, r) = ev::event(&mut *addr_of_mut!(ST), t, a, b2);
        if redraw_now { redraw(pitch); } else if (ox, oy) != (ST.mx, ST.my) { present(pitch, ox, oy, 14, 18); cursor(pitch, ST.mx, ST.my); }
        if t == 100 { lrp = rp; if !ST.sysok { ST.sysok = true; ST.sysv = [MSG[3], MSG[4], MSG[5]]; ev::audit_boot(&mut *addr_of_mut!(ST)); redraw(pitch); } else { let nb = MSG[3] >> 24; if nb != ST.sysv[0] >> 24 { ST.sysv[0] = (ST.sysv[0] & 0xFFFFFF) | (nb << 24); redraw(pitch); } } } else { MSG[0] = r; sys(10, rp, MSG.as_ptr() as u64, 8, 0, 0); }
        if lrp != 0 && ST.want >= 0 { MSG[0] = ST.want as u64; ST.want = -1; sys(10, lrp, MSG.as_ptr() as u64, 8, 0, 0); lrp = 0; redraw(pitch); } }
} }
