//! Integer-only 2D renderer of the desktop shell (no floating point, no allocation): alpha rounded rectangles with 1/16-pixel signed-distance anti-aliasing,
//! gradients, Roboto glyph blits, Material icon masks. The independent model is tools/check_desk.py (same formulas, different language).
use crate::assets::*;
pub const W: usize = 1024; pub const H: usize = 768;
pub type Buf = [u32; W * H];
/// the clip rectangle (x0, y0, x1, y1), exclusive upper bounds; every primitive stays inside it
pub static mut CLIP: [i32; 4] = [0, 0, W as i32, H as i32];
#[inline(always)] fn cl() -> [i32; 4] { unsafe { CLIP } }
pub fn set_clip(x: i32, y: i32, w: i32, h: i32) { unsafe { CLIP = [x.max(0), y.max(0), (x + w).min(W as i32), (y + h).min(H as i32)]; } }
pub fn clear_clip() { unsafe { CLIP = [0, 0, W as i32, H as i32]; } }
#[inline(always)] pub fn blend(p: &mut u32, rgb: u32, a: u32) {
    if a == 0 { return; }
    let d = *p; let mut o = 0xFF000000u32;
    for s in [16u32, 8, 0] { let sc = (rgb >> s) & 255; let dc = (d >> s) & 255; o |= ((dc * (255 - a) + sc * a + 127) / 255) << s; }
    *p = o;
}
pub fn isqrt(n: u32) -> u32 { let mut r = 0u32; let mut b = 1u32 << 15; while b != 0 { let t = r | b; if t * t <= n { r = t; } b >>= 1; } r }
#[inline(always)] fn lerp(a: u32, b: u32, t: u32, d: u32) -> u32 { let mut o = 0; for s in [16u32, 8, 0] { let x = (a >> s) & 255; let y = (b >> s) & 255; o |= ((x * (d - t) + y * t + d / 2) / d) << s; } o }
/// coverage 0..255 of a rounded box (centre cx,cy; half sizes hx,hy; radius r; all in 1/16 px) at the pixel centre (px,py) given in whole pixels
#[inline(always)] pub fn cover(px: i32, py: i32, cx: i32, cy: i32, hx: i32, hy: i32, r: i32) -> u32 {
    let dx = (px * 16 + 8 - cx).abs(); let dy = (py * 16 + 8 - cy).abs(); let a = dx - hx + r; let b = dy - hy + r;
    let ma = a.max(0); let mb = b.max(0);
    let l = if ma == 0 || mb == 0 { ma + mb } else { isqrt((ma * ma + mb * mb) as u32) as i32 };
    let sd = a.max(b).min(0) + l - r;
    ((8 - sd).clamp(0, 16) * 255 / 16) as u32
}
/// rounded rectangle: fill = (top rgb, bottom rgb, alpha), border = (rgb, alpha) 1 px (alpha 0 = none)
pub fn rrect(buf: &mut Buf, x: i32, y: i32, w: i32, h: i32, r: i32, f0: u32, f1: u32, fa: u32, brgb: u32, ba: u32) {
    let (cx, cy, hx, hy) = ((x * 2 + w) * 8, (y * 2 + h) * 8, w * 8, h * 8); let r16 = r * 16;
    let c = cl(); for py in y.max(c[1])..(y + h).min(c[3]) { let fc = if f0 == f1 { f0 } else { lerp(f0, f1, (py - y) as u32, h as u32) };
        for px in x.max(c[0])..(x + w).min(c[2]) {
            let co = cover(px, py, cx, cy, hx, hy, r16); if co == 0 { continue; }
            let ci = if ba != 0 { cover(px, py, cx, cy, hx - 16, hy - 16, (r16 - 16).max(0)) } else { co };
            let p = &mut buf[py as usize * W + px as usize];
            blend(p, fc, fa * ci / 255); if ba != 0 { blend(p, brgb, ba * co.saturating_sub(ci) / 255); }
        } }
}
pub fn rect(buf: &mut Buf, x: i32, y: i32, w: i32, h: i32, rgb: u32, a: u32) { let c = cl(); for py in y.max(c[1])..(y + h).min(c[3]) { for px in x.max(c[0])..(x + w).min(c[2]) { blend(&mut buf[py as usize * W + px as usize], rgb, a); } } }
fn rd(o: usize) -> i32 { i32::from_le_bytes([FONT[o], FONT[o + 1], FONT[o + 2], FONT[o + 3]]) }
fn glyph(face: usize, c: u8) -> [i32; 6] { let base = (2 * NFACES + (face * 95 + (c - 32) as usize) * 6) * 4; [rd(base), rd(base + 4), rd(base + 8), rd(base + 12), rd(base + 16), rd(base + 20)] }
pub fn text_w(face: usize, s: &str) -> i32 { s.bytes().filter(|&c| (32..127).contains(&c)).map(|c| glyph(face, c)[5]).sum() }
pub fn text(buf: &mut Buf, face: usize, x: i32, base: i32, s: &str, rgb: u32, a: u32) -> i32 {
    let mut pen = x; let cp = cl();
    for c in s.bytes() { if !(32..127).contains(&c) { continue; } let g = glyph(face, c);
        for gy in 0..g[2] { let py = base + g[4] + gy; if py < cp[1] || py >= cp[3] { continue; }
            for gx in 0..g[1] { let px = pen + g[3] + gx; if px < cp[0] || px >= cp[2] { continue; }
                let m = FONT[MASK_BASE + (g[0] + gy * g[1] + gx) as usize] as u32; blend(&mut buf[py as usize * W + px as usize], rgb, m * a / 255); } }
        pen += g[5]; }
    pen - x
}
pub fn text_c(buf: &mut Buf, face: usize, cx: i32, base: i32, s: &str, rgb: u32, a: u32) { let w = text_w(face, s); text(buf, face, cx - w / 2, base, s, rgb, a); }
pub fn icon(buf: &mut Buf, idx: usize, x: i32, y: i32, rgb: u32, a: u32) {
    let (off, sz) = ICON_TAB[idx]; let sz = sz as i32; let cp = cl();
    for gy in 0..sz { let py = y + gy; if py < cp[1] || py >= cp[3] { continue; } for gx in 0..sz { let px = x + gx; if px < cp[0] || px >= cp[2] { continue; }
        let m = ICONS[off + (gy * sz + gx) as usize] as u32; blend(&mut buf[py as usize * W + px as usize], rgb, m * a / 255); } }
}
/// the wallpaper: a near-black navy base with two soft radial glows (integer falloff, 1 - d^2/r^2 squared)
pub fn wallpaper(buf: &mut Buf) {
    for y in 0..H as i32 { for x in 0..W as i32 {
        let mut c = [7u32, 9, 22];
        for (gx, gy, rr, col, amp) in [(830i32, 650i32, 560i32, [96u32, 40, 150], 200u32), (120, 60, 430, [30, 60, 150], 150), (540, 380, 700, [40, 20, 80], 70)] {
            let d2 = (x - gx) * (x - gx) + (y - gy) * (y - gy); let r2 = rr * rr; if d2 >= r2 { continue; }
            let t = ((r2 - d2) as u64 * 255 / r2 as u64) as u32; let t = t * t / 255 * amp / 255;
            for k in 0..3 { c[k] = (c[k] * (255 - t) + col[k] * t + 127) / 255; } }
        buf[y as usize * W + x as usize] = 0xFF000000 | c[0] << 16 | c[1] << 8 | c[2]; } }
}
