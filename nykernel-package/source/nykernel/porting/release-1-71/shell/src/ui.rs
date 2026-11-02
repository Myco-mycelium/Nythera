//! ui.rs -- the Nyrqis shell's renderer (NUI-001): an Android-17 / Material 3 Expressive HOME SCREEN, drawn by the design rules of Apple's HIG.
//! `render()` is pure drawing into a pixel buffer, so the SAME source runs in the user-space `nyshell` process (into the FrameBuffer window) and in the host
//! tool `host/hostrender.rs` (into a Vec), where tools/check_shell.py compares it with an independent Python implementation of docs/ui/NUI-001.
//! Units: 1 dp = 1 px (density 1; a density factor is future work). No allocation, no floats, no statics.
use crate::font_data::GLYPHS;

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Theme { Light, Dark }

/// Material 3 colour ROLES (baseline scheme, static -- Android 17's wallpaper-derived dynamic colour is not implemented; the roles are what a dynamic scheme would replace).
#[derive(Clone, Copy)]
pub struct Colors {
    pub surface: u32, pub on_surface: u32, pub on_surface_variant: u32,
    pub container: u32, pub container_high: u32, pub container_highest: u32,
    pub primary: u32, pub on_primary: u32, pub primary_container: u32, pub on_primary_container: u32,
    pub secondary_container: u32, pub on_secondary_container: u32, pub tertiary_container: u32, pub on_tertiary_container: u32,
    pub outline_variant: u32,
}
pub const LIGHT: Colors = Colors {
    surface: 0xFEF7FF, on_surface: 0x1D1B20, on_surface_variant: 0x49454F, container: 0xF3EDF7, container_high: 0xECE6F0, container_highest: 0xE6E0E9,
    primary: 0x6750A4, on_primary: 0xFFFFFF, primary_container: 0xEADDFF, on_primary_container: 0x21005D,
    secondary_container: 0xE8DEF8, on_secondary_container: 0x1D192B, tertiary_container: 0xFFD8E4, on_tertiary_container: 0x31111D, outline_variant: 0xCAC4D0,
};
pub const DARK: Colors = Colors {
    surface: 0x141218, on_surface: 0xE6E0E9, on_surface_variant: 0xCAC4D0, container: 0x211F26, container_high: 0x2B2930, container_highest: 0x36343B,
    primary: 0xD0BCFF, on_primary: 0x381E72, primary_container: 0x4F378B, on_primary_container: 0xEADDFF,
    secondary_container: 0x4A4458, on_secondary_container: 0xE8DEF8, tertiary_container: 0x633B48, on_tertiary_container: 0xFFD8E4, outline_variant: 0x49454F,
};

/// Window size classes (Android / Material 3 adaptive): by WIDTH in dp.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum SizeClass { Compact, Medium, Expanded }
pub fn size_class(w: i32) -> SizeClass { if w < 600 { SizeClass::Compact } else if w < 840 { SizeClass::Medium } else { SizeClass::Expanded } }

/// A pixel buffer: `pitch` is in PIXELS. Drawing is clipped to [0,w) x [0,h).
pub struct Fb { pub base: *mut u32, pub pitch: usize, pub w: i32, pub h: i32 }
impl Fb {
    fn fill_span(&mut self, x0: i32, x1: i32, y: i32, c: u32) {
        if y < 0 || y >= self.h { return; }
        let a = if x0 < 0 { 0 } else { x0 }; let b = if x1 > self.w { self.w } else { x1 };
        let mut x = a;
        while x < b { unsafe { self.base.add(y as usize * self.pitch + x as usize).write_volatile(c); } x += 1; }
    }
    pub fn rect(&mut self, x: i32, y: i32, w: i32, h: i32, c: u32) { let mut j = 0; while j < h { self.fill_span(x, x + w, y + j, c); j += 1; } }
    /// Rounded rectangle, corner radius r (clamped to min(w,h)/2). A pixel (px,py) of a corner is inside when its CENTRE is within the circle of radius r:
    /// (2px+1-2r)^2 + (2py+1-2r)^2 <= 4r^2 (integer arithmetic, so every implementation agrees bit for bit).
    pub fn round(&mut self, x: i32, y: i32, w: i32, h: i32, r: i32, c: u32) {
        let mut r = r; if r > w / 2 { r = w / 2; } if r > h / 2 { r = h / 2; }
        let mut j = 0;
        while j < h {
            let rj = if j < r { j } else if j >= h - r { h - 1 - j } else { r };
            let mut inset = 0;
            if rj < r {
                let dy = 2 * rj + 1 - 2 * r;
                while { let dx = 2 * inset + 1 - 2 * r; dx * dx + dy * dy > 4 * r * r } { inset += 1; }
            }
            self.fill_span(x + inset, x + w - inset, y + j, c);
            j += 1;
        }
    }
}

fn glyph_index(ch: u8) -> usize {
    match ch { b'A'..=b'Z' => (ch - b'A') as usize, b'0'..=b'9' => 27 + (ch - b'0') as usize, b':' => 37, _ => 26 }
}
/// Width in px of `s` at `scale` (6-cell advance, last column of spacing not counted).
pub fn text_w(s: &[u8], scale: i32) -> i32 { s.len() as i32 * 6 * scale - scale }
pub fn text(fb: &mut Fb, x: i32, y: i32, s: &[u8], scale: i32, c: u32) {
    let mut i = 0;
    while i < s.len() {
        let g = &GLYPHS[glyph_index(s[i])];
        let mut row = 0;
        while row < 7 {
            let bits = g[row];
            let mut col = 0;
            while col < 5 {
                if (bits >> (4 - col)) & 1 == 1 { fb.rect(x + (i as i32 * 6 + col as i32) * scale, y + row as i32 * scale, scale, scale, c); }
                col += 1;
            }
            row += 1;
        }
        i += 1;
    }
}
fn text_c(fb: &mut Fb, cx: i32, y: i32, s: &[u8], scale: i32, c: u32) { let w = text_w(s, scale); text(fb, cx - w / 2, y, s, scale, c); }

pub const APPS: [&[u8]; 8] = [b"PHONE", b"FILES", b"CLOCK", b"NOTES", b"MAPS", b"MAIL", b"CAMERA", b"PC"];

/// Everything the layout decided (also returned so the host check can assert the HIG/Material invariants on the real numbers).
pub struct Info { pub class: SizeClass, pub margin: i32, pub cols: i32, pub rows: i32, pub tiles: i32 }

pub fn render(fb: &mut Fb, theme: Theme) -> Info {
    let k = match theme { Theme::Light => LIGHT, Theme::Dark => DARK };
    let (w, h) = (fb.w, fb.h);
    let class = size_class(w);
    let margin = if class == SizeClass::Compact { 16 } else { 24 };
    fb.rect(0, 0, w, h, k.surface);
    // ---- status bar (24 dp, edge-to-edge): clock left; signal + battery right ----
    text(fb, margin, 5, b"12:30", 2, k.on_surface);
    let bx = w - margin - 24;
    fb.round(bx, 6, 22, 11, 3, k.container_highest);
    fb.round(bx + 2, 8, 12, 7, 1, k.on_surface);
    fb.rect(bx + 22, 9, 2, 5, k.on_surface);
    let sx = bx - 8 - 18;
    let mut i = 0;
    while i < 4 { let bh = 4 + 2 * i; fb.round(sx + i * 5, 17 - bh, 3, bh, 1, if i < 3 { k.on_surface } else { k.outline_variant }); i += 1; }
    // ---- panes ----
    let (left_x, left_w, right_x, right_w) = if class == SizeClass::Compact { (margin, w - 2 * margin, margin, w - 2 * margin) } else {
        let lw = if class == SizeClass::Expanded { 360 } else { (w - 3 * margin) * 2 / 5 };
        (margin, lw, margin + lw + margin, w - (margin + lw + margin) - margin)
    };
    // ---- clock widget (28 dp corners) ----
    let cy = 40;
    fb.round(left_x, cy, left_w, 112, 28, k.container);
    text(fb, left_x + 16, cy + 20, b"12:30", 6, k.on_surface);
    text(fb, left_x + 16, cy + 74, b"THU 22 OCT", 2, k.on_surface_variant);
    // ---- quick-settings tiles (medium/expanded only): 56 dp pills, 8 dp apart; active = primary ----
    let mut tiles = 0;
    let bottom = h - 24 - 16;
    if class != SizeClass::Compact {
        let names: [&[u8]; 3] = [b"INTERNET", b"BLUETOOTH", b"TORCH"];
        let mut t = 0;
        while t < 3 {
            let ty = cy + 112 + 16 + t * 64;
            if ty + 56 <= bottom {
                let on = t < 2;
                let (fill, ic, ig, tx) = if on { (k.primary, k.on_primary, k.primary, k.on_primary) } else { (k.container_highest, k.outline_variant, k.on_surface, k.on_surface) };
                fb.round(left_x, ty, left_w, 56, 28, fill);
                fb.round(left_x + 12, ty + 12, 32, 32, 16, ic);
                text_c(fb, left_x + 28, ty + 12 + 5, &names[t as usize][..1], 3, ig);
                text(fb, left_x + 12 + 32 + 12, ty + 21, names[t as usize], 2, tx);
                tiles += 1;
            }
            t += 1;
        }
    }
    // ---- app grid: cells of (pane width / cols) x 96 dp; icon 56 dp (20 dp corners) + 14-px label ----
    let cols = if class == SizeClass::Expanded { 6 } else { 4 };
    let grid_y = if class == SizeClass::Compact { cy + 112 + 16 } else { 40 };
    let search_y = h - 24 - 16 - 56;
    let mut rows = (search_y - 8 - grid_y) / 96; if rows > 2 { rows = 2; } if rows < 0 { rows = 0; }
    let cell_w = right_w / cols;
    let mut n = 0;
    while n < 8 {
        let (rr, cc) = (n / cols, n % cols);
        if rr < rows {
            let cx = right_x + cc * cell_w; let cyy = grid_y + rr * 96;
            let (fill, ink) = match n % 3 { 0 => (k.primary_container, k.on_primary_container), 1 => (k.secondary_container, k.on_secondary_container), _ => (k.tertiary_container, k.on_tertiary_container) };
            let ix = cx + (cell_w - 56) / 2;
            fb.round(ix, cyy + 8, 56, 56, 20, fill);
            text_c(fb, ix + 28, cyy + 8 + 17, &APPS[n as usize][..1], 3, ink);
            text_c(fb, cx + cell_w / 2, cyy + 8 + 56 + 8, APPS[n as usize], 2, k.on_surface);
        }
        n += 1;
    }
    // ---- search pill (56 dp, fully rounded), bottom of the right pane, at most 560 dp wide ----
    let sw = if right_w > 560 { 560 } else { right_w };
    let sxp = right_x + (right_w - sw) / 2;
    if search_y >= 40 {
        fb.round(sxp, search_y, sw, 56, 28, k.container_high);
        fb.round(sxp + 8, search_y + 8, 40, 40, 20, k.primary);
        text_c(fb, sxp + 28, search_y + 8 + 10, b"N", 3, k.on_primary);
        text(fb, sxp + 8 + 40 + 16, search_y + 21, b"SEARCH", 2, k.on_surface_variant);
    }
    // ---- gesture navigation pill (108 x 4) in the 24 dp bottom inset ----
    fb.round((w - 108) / 2, h - 14, 108, 4, 2, k.on_surface);
    Info { class, margin, cols, rows, tiles }
}

/// Hash of the whole surface: h = h*31 + (pixel & 0xFFFFFF), 32-bit wrapping, row-major (same as the display service's band hash).
pub fn hash(fb: &Fb) -> u32 {
    let mut hh: u32 = 0; let mut y = 0;
    while y < fb.h { let mut x = 0; while x < fb.w { let p = unsafe { fb.base.add(y as usize * fb.pitch + x as usize).read_volatile() } & 0xFFFFFF; hh = hh.wrapping_mul(31).wrapping_add(p); x += 1; } y += 1; }
    hh
}
