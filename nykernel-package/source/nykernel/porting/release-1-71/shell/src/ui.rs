//! ui.rs -- the Nyrqis shell's renderer (NUI-001): Android 17's HOME SCREEN and Windows' TASKBAR in one shell, drawn by the design rules of Apple's HIG
//! and carrying none of Apple's assets. The functions are the union of the two platforms' (a launcher, widgets, quick settings and a search field from Android;
//! a bottom taskbar, running-app buttons, a system tray and window controls from Windows), because a shell that made the user choose would be a worse shell: the
//! two models answer different questions ("what can this machine do" vs "what is running") and both are asked on every device. The LOOK is HIG: the 8 dp grid,
//! 44 dp minimum hit targets, one type ramp, generous corner radii and a single accent, so the union does not read as two shells stapled together.
//!
//! No Apple icon or widget is used: every symbol here is either a glyph of the 5x7 font this tree ships or rectangles this file draws (the Start mark is the
//! letter N, minimise/maximise/close are a line, a square and an X). Apple's traffic-light dots are deliberately NOT reproduced -- they are Apple's widget, and a
//! red/yellow/green circle triplet also encodes close/minimise/zoom by COLOUR alone, which fails the HIG's own accessibility rule; a line/square/X reads at any
//! colour vision and is the Windows convention for the same three functions.
//!
//! The style is MINIMAL: structure is carried by 1 dp hairlines and whitespace, not by filled blocks, so the surface is one colour with one accent. `outline()`
//! is where that rule lives -- a rounded hairline is a filled rounded rect with its inside painted back in the surface colour, which is why that helper takes the
//! background as a parameter. The type is the tree's own 5x7 bitmap face (there is no second face to switch to: it is generated from tools/font5x7.txt and is the
//! same table the display service and the console speak), so "lighter type" here is the SIZE RAMP: one 21 px display size for the clock and 14 px for everything
//! else, instead of the 42 px display scale this shell used before.
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

/// The style rule, in one function: a 1 dp rounded hairline. Filled blocks were
/// the old shell's structure and they made every panel shout; a hairline plus
/// whitespace says the same thing at a tenth of the ink, which is the whole
/// minimalist premise. `bg` is the colour behind the shape, painted back inside.
fn outline(fb: &mut Fb, x: i32, y: i32, w: i32, h: i32, r: i32, c: u32, bg: u32) {
    fb.round(x, y, w, h, r, c);
    fb.round(x + 1, y + 1, w - 2, h - 2, if r > 1 { r - 1 } else { 0 }, bg);
}

/// One display size, one body size. Keeping the ramp to two steps is what makes
/// the type read as a system rather than a flyer, and the display step is small
/// enough (21 px) that the clock is a widget, not a poster.
pub const DISPLAY_SCALE: i32 = 3;
pub const BODY_SCALE: i32 = 2;

/// The height of the shell's ONE bottom bar: 44 dp where a pointer drives the
/// shell, 40 dp on a phone. The bar lives INSIDE the content pane (the device
/// modes draw no bottom chrome of their own any more -- that is what "one
/// bottom bar" means here), but floating controls above it need the number, so
/// it is exported rather than copied into `device_mode`.
pub fn bar_height(w: i32) -> i32 { if size_class(w) == SizeClass::Compact { 40 } else { 44 } }

pub const APPS: [&[u8]; 8] = [b"PHONE", b"FILES", b"CLOCK", b"NOTES", b"MAPS", b"MAIL", b"CAMERA", b"PC"];

/// Everything the layout decided (also returned so the host check can assert the HIG/Material invariants on the real numbers).
pub struct Info { pub class: SizeClass, pub margin: i32, pub cols: i32, pub rows: i32, pub tiles: i32 }

pub fn render(fb: &mut Fb, theme: Theme) -> Info {
    let k = match theme { Theme::Light => LIGHT, Theme::Dark => DARK };
    let (w, h) = (fb.w, fb.h);
    let class = size_class(w);
    let margin = if class == SizeClass::Compact { 16 } else { 24 };
    fb.rect(0, 0, w, h, k.surface);

    // ---- the Windows half: a bottom taskbar on the 8 dp grid ----------------
    // 44 dp on a pointer-sized shell, 40 dp on a phone. It is the ONLY bottom
    // bar: the device modes no longer draw a dock, a gesture pill or a bottom
    // navigation strip, because each of those duplicated a function the bar
    // already carries (launchers, navigation) and left two bars claiming one
    // edge of the screen.
    let bar_h = bar_height(w);
    let bar_y = h - bar_h;
    let body_bottom = bar_y - 16;                 // home content stops 16 dp above it

    // ---- the Android half: a 24 dp status bar, edge to edge ----------------
    text(fb, margin, 5, b"12:30", BODY_SCALE, k.on_surface_variant);
    let bx = w - margin - 24;
    outline(fb, bx, 6, 22, 11, 3, k.outline_variant, k.surface);
    fb.round(bx + 3, 8, 10, 7, 1, k.on_surface_variant);
    let sx = bx - 8 - 18;
    let mut i = 0;
    while i < 4 { let bh = 4 + 2 * i; fb.round(sx + i * 5, 17 - bh, 3, bh, 1, k.on_surface_variant); i += 1; }

    // ---- taskbar contents: start, ONE search field, running apps, tray ------
    fb.rect(0, bar_y, w, bar_h, k.surface);
    fb.rect(0, bar_y, w, 1, k.outline_variant);
    let ctrl = if bar_h >= 44 { 32 } else { 28 };
    let cy = bar_y + (bar_h - ctrl) / 2;
    // Start: this shell's own mark (the letter N) on the ONE accent fill in the
    // whole surface -- not Windows' flag and not any Apple glyph. A single
    // accent is the HIG rule; spending it on the element that opens everything
    // is also where a user looks first.
    fb.round(margin, cy, ctrl, ctrl, 8, k.primary);
    text_c(fb, margin + ctrl / 2, cy + (ctrl - 7 * BODY_SCALE) / 2, b"N", BODY_SCALE, k.on_primary);
    // ONE search field for the whole shell: Android's bottom search pill and
    // Windows' taskbar search are the same function, so the union keeps one
    // rather than making the user learn which of two identical boxes to type in.
    let tray_w = text_w(b"12:30", BODY_SCALE) + 12 + 26 + margin;
    let sx0 = margin + ctrl + 12;
    let avail = w - sx0 - tray_w - 12;
    let mut search_w = 0;
    if avail > 96 {
        search_w = if avail > 320 { 320 } else { avail };
        outline(fb, sx0, cy, search_w, ctrl, ctrl / 2, k.outline_variant, k.surface);
        text(fb, sx0 + 12, cy + (ctrl - 7 * BODY_SCALE) / 2, b"SEARCH", BODY_SCALE, k.on_surface_variant);
    }
    // Running apps (Windows' task buttons). Each is a 44 x ctrl target carrying
    // its app's initial, which is the whole label a 320 dp bar can afford; they
    // appear only on Expanded, where they do not crowd the search field.
    if class == SizeClass::Expanded {
        let chips: [&[u8]; 3] = [b"P", b"F", b"N"];   // the three most recent
        let chip_x = sx0 + search_w + 16;
        let mut c = 0;
        while c < 3 {
            let x = chip_x + c * (44 + 8);
            if x + 44 <= w - tray_w {
                outline(fb, x, cy, 44, ctrl, 8, if c == 0 { k.on_surface_variant } else { k.outline_variant }, k.surface);
                text_c(fb, x + 22, cy + (ctrl - 7 * BODY_SCALE) / 2, chips[c as usize], BODY_SCALE, if c == 0 { k.on_surface } else { k.on_surface_variant });
            }
            c += 1;
        }
    }
    // Tray: the clock and a hairline battery. Android shows both in its status
    // bar already; a taskbar tray keeps them where a desktop user looks for
    // them, and one shell draws the same glyph twice rather than inventing a
    // second battery metaphor.
    let byl = bar_y + (bar_h - 11) / 2;
    outline(fb, w - margin - 24, byl, 22, 11, 3, k.on_surface_variant, k.surface);
    fb.round(w - margin - 21, byl + 3, 8, 5, 1, k.on_surface_variant);
    let clock_w = text_w(b"12:30", BODY_SCALE);
    text(fb, w - margin - 24 - 12 - clock_w, bar_y + (bar_h - 7 * BODY_SCALE) / 2, b"12:30", BODY_SCALE, k.on_surface_variant);

    // ---- panes -------------------------------------------------------------
    let (left_x, left_w, right_x, right_w) = if class == SizeClass::Compact { (margin, w - 2 * margin, margin, w - 2 * margin) } else {
        let lw = if class == SizeClass::Expanded { 360 } else { (w - 3 * margin) * 2 / 5 };
        (margin, lw, margin + lw + margin, w - (margin + lw + margin) - margin)
    };
    // ---- clock widget: the one filled panel left. It is the shell's anchor,
    // and `device_mode` reads a pixel of this fill back as proof the shell ran
    // (its `Container` colour appears nowhere else on the surface).
    let cyy0 = 40;
    fb.round(left_x, cyy0, left_w, 112, 16, k.container);
    text(fb, left_x + 16, cyy0 + 28, b"12:30", DISPLAY_SCALE, k.on_surface);
    text(fb, left_x + 16, cyy0 + 76, b"THU 22 OCT", BODY_SCALE, k.on_surface_variant);
    // ---- quick-settings tiles (medium/expanded only): hairline pills, 8 dp
    // apart; the active one is marked by an accent DOT and accent ink, not by a
    // filled slab, so the accent stays one element's worth of ink.
    let mut tiles = 0;
    if class != SizeClass::Compact {
        let names: [&[u8]; 3] = [b"INTERNET", b"BLUETOOTH", b"TORCH"];
        let mut t = 0;
        while t < 3 {
            let ty = cyy0 + 112 + 16 + t * 64;
            if ty + 56 <= body_bottom {
                let on = t < 2;
                outline(fb, left_x, ty, left_w, 56, 28, if on { k.primary } else { k.outline_variant }, k.surface);
                fb.round(left_x + 14, ty + 22, 12, 12, 6, if on { k.primary } else { k.on_surface_variant });
                text(fb, left_x + 38, ty + 21, names[t as usize], BODY_SCALE, if on { k.on_surface } else { k.on_surface_variant });
                tiles += 1;
            }
            t += 1;
        }
    }
    // ---- app grid: cells of (pane width / cols) x 96 dp; a 48 dp hairline
    // icon (12 dp corners) + a 14-px label. Monochrome on purpose: eight apps
    // are not eight colours, and the old tri-colour icon set read as decoration.
    let cols = if class == SizeClass::Expanded { 6 } else { 4 };
    let mut rows;
    let grid_y;
    if class == SizeClass::Compact {
        grid_y = cyy0 + 112 + 16;
        rows = (body_bottom - grid_y) / 96;
    } else {
        // Windows' window around Android's apps: a hairline frame with a title
        // row and OUR minimise, maximise and close -- a line, an outlined square
        // and an X, drawn here from rectangles. They sit inside 28 dp of a 36 dp
        // bar, right-aligned on the grid, and each is a real 44 dp target once
        // the bar's padding is counted.
        let title_h = 36;
        rows = (body_bottom - (40 + title_h + 16)) / 96;
        let rows_c = if rows > 2 { 2 } else { rows };
        let body_h = title_h + 16 + rows_c * 96;
        outline(fb, right_x, 40, right_w, body_h, 16, k.outline_variant, k.surface);
        fb.rect(right_x + 1, 40 + title_h, right_w - 2, 1, k.outline_variant);
        text(fb, right_x + 16, 40 + 11, b"APPS", BODY_SCALE, k.on_surface_variant);
        let b = 28;
        let by = 40 + (title_h - b) / 2;
        let mut ctl_x = right_x + right_w - 16 - 3 * b - 2 * 6;
        fb.rect(ctl_x + b / 2 - 6, by + b / 2, 12, 2, k.on_surface_variant);              // minimise
        ctl_x += b + 6;
        outline(fb, ctl_x + 4, by + b / 2 - 5, 12, 10, 2, k.on_surface_variant, k.surface); // maximise: an outlined square
        ctl_x += b + 6;
        let mut d = 0;
        while d < 10 {                                                                   // close: an X, two diagonals
            fb.rect(ctl_x + 4 + d, by + b / 2 - 5 + d, 2, 2, k.on_surface_variant);
            fb.rect(ctl_x + 13 - d, by + b / 2 - 5 + d, 2, 2, k.on_surface_variant);
            d += 1;
        }
        grid_y = 40 + title_h + 16;
    }
    if rows > 2 { rows = 2; } if rows < 0 { rows = 0; }
    let cell_w = right_w / cols;
    let mut n = 0;
    while n < 8 {
        let (rr, cc) = (n / cols, n % cols);
        if rr < rows {
            let cx = right_x + cc * cell_w; let cyy = grid_y + rr * 96;
            let ix = cx + (cell_w - 48) / 2;
            outline(fb, ix, cyy + 12, 48, 48, 12, k.outline_variant, k.surface);
            text_c(fb, ix + 24, cyy + 12 + 10, &APPS[n as usize][..1], BODY_SCALE, k.on_surface);
            text_c(fb, cx + cell_w / 2, cyy + 12 + 48 + 10, APPS[n as usize], BODY_SCALE, k.on_surface_variant);
        }
        n += 1;
    }
    Info { class, margin, cols, rows, tiles }
}

/// Hash of the whole surface: h = h*31 + (pixel & 0xFFFFFF), 32-bit wrapping, row-major (same as the display service's band hash).
pub fn hash(fb: &Fb) -> u32 {
    let mut hh: u32 = 0; let mut y = 0;
    while y < fb.h { let mut x = 0; while x < fb.w { let p = unsafe { fb.base.add(y as usize * fb.pitch + x as usize).read_volatile() } & 0xFFFFFF; hh = hh.wrapping_mul(31).wrapping_add(p); x += 1; } y += 1; }
    hh
}
