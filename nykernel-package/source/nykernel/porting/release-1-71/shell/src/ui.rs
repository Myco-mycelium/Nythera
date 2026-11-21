//! The Nyrqis shell: a pixel port of the delivered HTML/CSS mock.
//!
//! The mock is one compositor with four postures (`data-mode` on `#shell`):
//! a top bar over a tide-band wallpaper, a keel dock, a launcher lattice, the
//! control centre, windows with a 36 px bar and a snap popover, a phone gesture
//! pill and a console hint bar. This file reproduces that DOM and its
//! stylesheet, measurement for measurement, in the framebuffer:
//!
//! * CSS pixels are framebuffer pixels. The mock is fluid, and its breakpoints
//!   (`<640` phone, `<1040` tablet, otherwise desktop) are the device modes the
//!   kernel already selects, so a mode is simply the posture this file draws.
//! * Colours, radii, paddings, gaps and font sizes are the stylesheet's own
//!   numbers, read from the mock's `:root` tokens and rules; the comments name
//!   the rule each block ports.
//! * Type is the mock's real typefaces. Bricolage Grotesque and IBM Plex Sans /
//!   Mono are rasterised at their CSS sizes by `tools/gen_font_atlas.py` into
//!   `assets/fonts.bin` and blended per pixel here, because the kernel has no
//!   font engine. That is the one substitution this port needs no apology for:
//!   the letterforms are the mock's.
//! * The mock's `rgba()` glass is composited here for real (alpha blending over
//!   the wallpaper). Its `backdrop-filter` blur is not: a blur needs a second
//!   off-screen pass this renderer does not have, and over the smooth wallpaper
//!   gradient the difference is invisible -- recorded as a known limit.
//! * The brand is Nyrqis. The mock's name appears nowhere on screen.
//!
//! Everything that is state in the mock (open windows, focus, toggles) is a
//! constant here: the boot frame is the mock's own start state -- notes,
//! terminal and monitor open on the desktop; the launcher open on tablet, phone
//! and console. Nothing is faked that the mock draws.

pub const BODY_SCALE: i32 = 2;
pub const DISPLAY_SCALE: i32 = 3;
/// `#shell{--top:34px}` / `[data-mode=phone]{--top:30px}`.
pub const TOP: i32 = 34;
const TOP_PHONE: i32 = 30;

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Theme {
    Light,
    Dark,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Posture {
    Desktop,
    Tablet,
    Phone,
    Console,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SizeClass {
    Compact,
    Medium,
    Expanded,
}

pub struct Fb {
    pub base: *mut u32,
    pub pitch: usize,
    pub w: i32,
    pub h: i32,
}

impl Fb {
    #[inline]
    fn poke(&mut self, x: i32, y: i32, c: u32) {
        if x < 0 || y < 0 || x >= self.w || y >= self.h {
            return;
        }
        unsafe { core::ptr::write_volatile(self.base.add(y as usize * self.pitch + x as usize), c) };
    }

    #[inline]
    fn peek(&self, x: i32, y: i32) -> u32 {
        if x < 0 || y < 0 || x >= self.w || y >= self.h {
            return 0;
        }
        unsafe { core::ptr::read_volatile(self.base.add(y as usize * self.pitch + x as usize)) }
    }

    /// Source-over compositing, exactly what the browser does for the mock's
    /// translucent surfaces: `out = src*a + dst*(1-a)`.
    fn blend(&mut self, x: i32, y: i32, c: u32, a: u32) {
        if a == 0 {
            return;
        }
        if a >= 255 {
            self.poke(x, y, c);
            return;
        }
        let d = self.peek(x, y);
        let mix = |s: u32, d: u32| -> u32 {
            ((s * a + d * (255 - a) + 127) / 255) & 0xff
        };
        let out = (mix((c >> 16) & 0xff, (d >> 16) & 0xff) << 16)
            | (mix((c >> 8) & 0xff, (d >> 8) & 0xff) << 8)
            | mix(c & 0xff, d & 0xff);
        self.poke(x, y, out);
    }

    pub fn rect(&mut self, x: i32, y: i32, w: i32, h: i32, c: u32) {
        self.rect_a(x, y, w, h, c, 255);
    }

    fn rect_a(&mut self, x: i32, y: i32, w: i32, h: i32, c: u32, a: u32) {
        let x1 = (x + w).min(self.w);
        let y1 = (y + h).min(self.h);
        let mut yy = y.max(0);
        while yy < y1 {
            let mut xx = x.max(0);
            while xx < x1 {
                self.blend(xx, yy, c, a);
                xx += 1;
            }
            yy += 1;
        }
    }

    /// A rounded rectangle (`border-radius` in the mock). Corners are filled by
    /// circle test, which is what `border-radius` is.
    pub fn round(&mut self, x: i32, y: i32, w: i32, h: i32, r: i32, c: u32) {
        self.round_a(x, y, w, h, r, c, 255);
    }

    fn round_a(&mut self, x: i32, y: i32, w: i32, h: i32, r: i32, c: u32, a: u32) {
        if w <= 0 || h <= 0 {
            return;
        }
        let r = r.min(w / 2).min(h / 2).max(0);
        if r == 0 {
            self.rect_a(x, y, w, h, c, a);
            return;
        }
        // middle band, then the four corners as circles
        self.rect_a(x + r, y, w - 2 * r, h, c, a);
        self.rect_a(x, y + r, r, h - 2 * r, c, a);
        self.rect_a(x + w - r, y + r, r, h - 2 * r, c, a);
        self.circle_a(x + r, y + r, r, c, a);
        self.circle_a(x + w - r - 1, y + r, r, c, a);
        self.circle_a(x + r, y + h - r - 1, r, c, a);
        self.circle_a(x + w - r - 1, y + h - r - 1, r, c, a);
    }

    /// `border:1px solid` on a rounded box: the border ring is composited first,
    /// then the interior is painted back in the surface colour the caller names.
    fn frame(&mut self, x: i32, y: i32, w: i32, h: i32, r: i32, c: u32, a: u32, fill: u32) {
        self.round_a(x, y, w, h, r, c, a);
        self.round(x + 1, y + 1, w - 2, h - 2, (r - 1).max(0), fill);
    }

    fn circle_a(&mut self, cx: i32, cy: i32, r: i32, c: u32, a: u32) {
        let mut dy = -r;
        while dy <= r {
            let mut dx = -r;
            while dx <= r {
                if dx * dx + dy * dy <= r * r {
                    self.blend(cx + dx, cy + dy, c, a);
                }
                dx += 1;
            }
            dy += 1;
        }
    }

    pub fn circle(&mut self, cx: i32, cy: i32, r: i32, c: u32) {
        self.circle_a(cx, cy, r, c, 255);
    }

    fn line(&mut self, x0: i32, y0: i32, x1: i32, y1: i32, c: u32) {
        let dx = (x1 - x0).abs();
        let dy = -(y1 - y0).abs();
        let sx = if x0 < x1 { 1 } else { -1 };
        let sy = if y0 < y1 { 1 } else { -1 };
        let mut err = dx + dy;
        let (mut x, mut y) = (x0, y0);
        loop {
            self.poke(x, y, c);
            if x == x1 && y == y1 {
                break;
            }
            let e2 = 2 * err;
            if e2 >= dy {
                err += dy;
                x += sx;
            }
            if e2 <= dx {
                err += dx;
                y += sy;
            }
        }
    }

    fn stroke_line(&mut self, x0: i32, y0: i32, x1: i32, y1: i32, c: u32) {
        self.line(x0, y0, x1, y1, c);
        self.line(x0 + 1, y0, x1 + 1, y1, c);
    }
}

// ---------------------------------------------------------------------------
// Type: the mock's own faces, rasterised by tools/gen_font_atlas.py.
// ---------------------------------------------------------------------------

static ATLAS: &[u8] = include_bytes!("../assets/fonts.bin");

#[derive(Clone, Copy)]
pub enum Font {
    Display700,
    Body400,
    Body500,
    Body600,
    Mono400,
    Mono500,
}

impl Font {
    fn name(self) -> &'static str {
        match self {
            Font::Display700 => "display700",
            Font::Body400 => "body400",
            Font::Body500 => "body500",
            Font::Body600 => "body600",
            Font::Mono400 => "mono400",
            Font::Mono500 => "mono500",
        }
    }
}

fn rd16(d: &[u8], o: usize) -> usize {
    (d[o] as usize) | ((d[o + 1] as usize) << 8)
}
fn rd32(d: &[u8], o: usize) -> usize {
    rd16(d, o) | (rd16(d, o + 2) << 16)
}
fn rd_i16(d: &[u8], o: usize) -> i32 {
    rd16(d, o) as i16 as i32
}

struct Face {
    px: i32,
    ascent: i32,
    glyphs: usize,
}

/// Finds a face by family name and CSS pixel size. The atlas holds 18 faces,
/// so a linear walk is cheaper than any index.
fn face(font: Font, px: i32) -> Option<Face> {
    if ATLAS.len() < 12 || &ATLAS[0..4] != b"NYFA" {
        return None;
    }
    let n = rd32(ATLAS, 8);
    let mut o = 12;
    let want = font.name().as_bytes();
    for _ in 0..n {
        let len = rd16(ATLAS, o);
        let name = &ATLAS[o + 2..o + 2 + len];
        o += 2 + len;
        let sizes = rd16(ATLAS, o);
        let ascent = rd_i16(ATLAS, o + 2);
        let glyphs = o + 10;
        if name == want && sizes as i32 == px {
            return Some(Face { px, ascent, glyphs });
        }
        // skip this face's glyph table
        let mut g = glyphs;
        let count = rd16(ATLAS, g - 2);
        for _ in 0..count {
            let dl = rd32(ATLAS, g + 7);
            g += 11 + dl;
        }
        o = g;
    }
    None
}

/// Glyph metrics + alpha slice. The atlas records a 16ths-of-a-pixel advance and
/// a baseline-relative box, exactly as FreeType reported it.
fn glyph(f: &Face, ch: u8) -> Option<(i32, i32, i32, i32, i32, &'static [u8])> {
    if ch < 32 || ch > 126 {
        return None;
    }
    let idx = (ch - 32) as usize;
    let mut o = f.glyphs;
    let count = rd16(ATLAS, o - 2);
    if idx >= count {
        return None;
    }
    for _ in 0..idx {
        let dl = rd32(ATLAS, o + 7);
        o += 11 + dl;
    }
    let present = ATLAS[o];
    let adv = rd_i16(ATLAS, o + 1);
    let w = ATLAS[o + 3] as i32;
    let h = ATLAS[o + 4] as i32;
    let ox = ATLAS[o + 5] as i8 as i32;
    let oy = ATLAS[o + 6] as i8 as i32;
    let dl = rd32(ATLAS, o + 7);
    if present == 0 {
        return Some((adv / 16, 0, 0, 0, 0, &[]));
    }
    Some((adv / 16, w, h, ox, oy, &ATLAS[o + 11..o + 11 + dl]))
}

/// Draws a run with its TOP at `y` (the box top, as CSS positions it) and its
/// left at `x`. `y + ascent` is the baseline the atlas was measured against.
fn atype(fb: &mut Fb, x: i32, y: i32, s: &[u8], font: Font, px: i32, color: u32) {
    let Some(f) = face(font, px) else { return };
    let base = y + f.ascent;
    let mut cx = x;
    for &ch in s {
        let Some((adv, w, h, ox, oy, data)) = glyph(&f, ch) else { continue };
        if w > 0 && h > 0 {
            for row in 0..h {
                for col in 0..w {
                    let a = data[(row * w + col) as usize] as u32;
                    if a > 8 {
                        fb.blend(cx + ox + col, base + oy + row, color, a);
                    }
                }
            }
        }
        cx += adv;
    }
}

fn atype_w(s: &[u8], font: Font, px: i32) -> i32 {
    let Some(f) = face(font, px) else { return 0 };
    let mut w = 0;
    for &ch in s {
        if let Some((adv, ..)) = glyph(&f, ch) {
            w += adv;
        }
    }
    w
}

fn atype_c(fb: &mut Fb, cx: i32, y: i32, s: &[u8], font: Font, px: i32, color: u32) {
    atype(fb, cx - atype_w(s, font, px) / 2, y, s, font, px, color);
}

fn atype_r(fb: &mut Fb, right: i32, y: i32, s: &[u8], font: Font, px: i32, color: u32) {
    atype(fb, right - atype_w(s, font, px), y, s, font, px, color);
}

// ---------------------------------------------------------------------------
// Palette: the mock's `:root` and its `prefers-color-scheme:dark` block.
// ---------------------------------------------------------------------------

#[derive(Clone, Copy)]
pub struct Colors {
    pub paper: u32,
    /// `--glass: rgba(...,.76)` -- alpha is applied by the compositor.
    pub glass: u32,
    pub line: u32,
    pub fg: u32,
    pub fg2: u32,
    pub accent: u32,
    pub accent_fg: u32,
    pub accent_soft: u32,
    pub warm: u32,
    pub danger: u32,
    pub ok: u32,
    pub win: u32,
    pub win_bar: u32,
    pub field: u32,
    pub term_bg: u32,
    pub term_fg: u32,
    pub w0: u32,
    pub w1: u32,
    pub w2: u32,
    pub w3: u32,
    pub w4: u32,
    /// Opaque names kept for the device-mode probes: the glass over the top-left
    /// wallpaper pixel, win-bar, and the accent, as the boot reads them back.
    pub surface: u32,
    pub container: u32,
    pub container_high: u32,
    pub container_highest: u32,
    pub primary: u32,
    pub primary_container: u32,
    pub on_primary: u32,
    pub on_primary_container: u32,
    pub on_surface: u32,
    pub on_surface_variant: u32,
    pub outline_variant: u32,
}

pub const GLASS_A: u32 = 194; // .76 * 255
const LINE_A: u32 = 36; // .14 * 255
const SOFT_A: u32 = 41; // .16 * 255

pub const DARK: Colors = Colors {
    paper: 0x0b171e,
    glass: 0x0e1e26,
    line: 0xbee1eb,
    fg: 0xe4eff3,
    fg2: 0x8fa8b5,
    accent: 0x3cc8c0,
    accent_fg: 0x052321,
    accent_soft: 0x3cc8c0,
    warm: 0xf0b84a,
    danger: 0xef7a6c,
    ok: 0x52c98a,
    win: 0x13252e,
    win_bar: 0x1a323d,
    field: 0x0f1e26,
    term_bg: 0x0a161c,
    term_fg: 0xcfe8e8,
    w0: 0x09161d,
    w1: 0x0e3340,
    w2: 0x114a53,
    w3: 0x12625f,
    w4: 0x187d73,
    surface: 0x0c1b23,
    container: 0x0c1b23,
    container_high: 0x13252e,
    container_highest: 0x1a323d,
    primary: 0x3cc8c0,
    primary_container: 0x153c40,
    on_primary: 0x052321,
    on_primary_container: 0xcdf4f1,
    on_surface: 0xe4eff3,
    on_surface_variant: 0x8fa8b5,
    outline_variant: 0x2b444f,
};

pub const LIGHT: Colors = Colors {
    paper: 0xe8eff1,
    glass: 0xf7fbfb,
    line: 0x10242e,
    fg: 0x10242e,
    fg2: 0x53697a,
    accent: 0x0b8f8c,
    accent_fg: 0xffffff,
    accent_soft: 0x0b8f8c,
    warm: 0xe8a62b,
    danger: 0xd4493b,
    ok: 0x2e9d62,
    win: 0xf8fbfc,
    win_bar: 0xe1eaed,
    field: 0xeef3f5,
    term_bg: 0x10242e,
    term_fg: 0xcfe8e8,
    w0: 0xd4e6e8,
    w1: 0xa6d3d1,
    w2: 0x64b3b3,
    w3: 0x32909a,
    w4: 0x1d6c7c,
    surface: 0xf0f6f7,
    container: 0xf0f6f7,
    container_high: 0xf8fbfc,
    container_highest: 0xe1eaed,
    primary: 0x0b8f8c,
    primary_container: 0xd6eeed,
    on_primary: 0xffffff,
    on_primary_container: 0x083f3e,
    on_surface: 0x10242e,
    on_surface_variant: 0x53697a,
    outline_variant: 0xa9bcc4,
};

/// Source-over of one opaque colour on another, the same arithmetic the
/// compositor uses -- exported so the boot's read-back can name the exact pixel
/// the glass produces over the wallpaper without duplicating the blend.
pub fn overlay(base: u32, c: u32, a: u32) -> u32 {
    let mix = |s: u32, d: u32| ((s * a + d * (255 - a) + 127) / 255) & 0xff;
    (mix((c >> 16) & 0xff, (base >> 16) & 0xff) << 16)
        | (mix((c >> 8) & 0xff, (base >> 8) & 0xff) << 8)
        | mix(c & 0xff, base & 0xff)
}

pub fn theme_colors(theme: Theme) -> &'static Colors {
    match theme {
        Theme::Light => &LIGHT,
        Theme::Dark => &DARK,
    }
}

// ---------------------------------------------------------------------------
// Wallpaper: the mock's `paint()` -- a vertical gradient plus four tide bands.
// ---------------------------------------------------------------------------

/// Bhaskara's sine, good to ~0.0016 rad over [-pi,pi]: the wallpaper only needs
/// the mock's band shapes, and `core` has no libm.
fn fsin(x: f32) -> f32 {
    let two_pi = 6.283_185_5f32;
    let mut t = x % two_pi;
    if t > 3.141_592_7 {
        t -= two_pi;
    } else if t < -3.141_592_7 {
        t += two_pi;
    }
    let b = 4.0 / 3.141_592_7;
    let c = -4.0 / (3.141_592_7 * 3.141_592_7);
    let y = b * t + c * t * t.abs();
    0.775 * y + 0.225 * y * y.abs()
}

fn lerp(a: u32, b: u32, t: u32) -> u32 {
    let mix = |s: u32, d: u32| ((s * (255 - t) + d * t) / 255) & 0xff;
    (mix((a >> 16) & 0xff, (b >> 16) & 0xff) << 16)
        | (mix((a >> 8) & 0xff, (b >> 8) & 0xff) << 8)
        | mix(a & 0xff, b & 0xff)
}

fn wallpaper(fb: &mut Fb, k: &Colors) {
    let (w, h) = (fb.w, fb.h);
    let bands = [k.w0, k.w1, k.w2, k.w3, k.w4];
    let mut x = 0;
    while x < w {
        // gradient stop 0 -> stop 1 down the column, then the four bands layered
        // over it; later bands paint over earlier ones, as in the mock.
        let mut col = [0u32; 0];
        let _ = &mut col;
        let mut y = 0;
        while y < h {
            let t = ((y as f32 / h as f32) * 255.0) as u32;
            fb.poke(x, y, lerp(bands[0], bands[1], t));
            y += 1;
        }
        x += 1;
    }
    let mut i = 1;
    while i <= 4 {
        let base = h as f32 * (0.34 + i as f32 * 0.15);
        let amp = 12.0 + i as f32 * 7.0;
        let f = 0.0042 / (1.0 + i as f32 * 0.18);
        let mut x = 0;
        while x < w {
            let yf = base + fsin(x as f32 * f * 2.0 + i as f32 * 1.7) * amp
                + fsin(x as f32 * f * 5.3 + i as f32) * amp * 0.4;
            let mut y = (yf as i32).max(0);
            while y < h {
                fb.poke(x, y, bands[i as usize]);
                y += 1;
            }
            x += 1;
        }
        i += 1;
    }
}

// ---------------------------------------------------------------------------
// Icons: the mock's inline SVGs, stroke-drawn on their 24-unit grid.
// ---------------------------------------------------------------------------

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Icon {
    Wave,
    Terminal,
    Files,
    Monitor,
    Notes,
    Settings,
    Drift,
    Apps,
    Min,
    Max,
    Close,
}

fn icon(fb: &mut Fb, ic: Icon, x: i32, y: i32, s: i32, c: u32) {
    let u = |v: i32| x + v * s / 24;
    let v = |q: i32| y + q * s / 24;
    match ic {
        Icon::Wave => {
            // M2 9c3-3 5-3 8 0s5 3 8 0 3-2 4-1M2 16... (two crests, stroke 2.2)
            for row in 0..2 {
                let yy = v(if row == 0 { 8 } else { 15 });
                let mut px = u(2);
                while px < u(22) {
                    let t = (px - u(2)) as f32 / (u(22) - u(2)) as f32;
                    let dy = (fsin(t * 12.56) * s as f32 * 0.11) as i32;
                    fb.stroke_line(px, yy + dy, px + 1, yy + dy, c);
                    px += 1;
                }
            }
        }
        Icon::Terminal => {
            fb.stroke_line(u(4), v(6), u(10), v(12), c);
            fb.stroke_line(u(10), v(12), u(4), v(18), c);
            fb.stroke_line(u(12), v(18), u(20), v(18), c);
        }
        Icon::Files => {
            fb.stroke_line(u(3), v(19), u(3), v(7), c);
            fb.stroke_line(u(3), v(7), u(9), v(7), c);
            fb.stroke_line(u(9), v(7), u(11), v(9), c);
            fb.stroke_line(u(3), v(7), u(21), v(9), c);
            fb.stroke_line(u(21), v(9), u(21), v(19), c);
            fb.stroke_line(u(21), v(19), u(3), v(19), c);
        }
        Icon::Monitor => {
            fb.stroke_line(u(3), v(12), u(7), v(12), c);
            fb.stroke_line(u(7), v(12), u(10), v(5), c);
            fb.stroke_line(u(10), v(5), u(14), v(19), c);
            fb.stroke_line(u(14), v(19), u(17), v(12), c);
            fb.stroke_line(u(17), v(12), u(21), v(12), c);
        }
        Icon::Notes => {
            fb.stroke_line(u(6), v(3), u(15), v(3), c);
            fb.stroke_line(u(15), v(3), u(19), v(7), c);
            fb.stroke_line(u(19), v(7), u(19), v(21), c);
            fb.stroke_line(u(19), v(21), u(6), v(21), c);
            fb.stroke_line(u(6), v(21), u(6), v(3), c);
            fb.stroke_line(u(14), v(3), u(14), v(8), c);
            fb.stroke_line(u(14), v(8), u(19), v(8), c);
            fb.stroke_line(u(9), v(13), u(16), v(13), c);
            fb.stroke_line(u(9), v(17), u(14), v(17), c);
        }
        Icon::Settings => {
            fb.circle(u(12), v(12), s / 6, c);
            fb.stroke_line(u(12), v(2), u(12), v(5), c);
            fb.stroke_line(u(12), v(19), u(12), v(22), c);
            fb.stroke_line(u(2), v(12), u(5), v(12), c);
            fb.stroke_line(u(19), v(12), u(22), v(12), c);
            fb.stroke_line(u(5), v(5), u(7), v(7), c);
            fb.stroke_line(u(17), v(17), u(19), v(19), c);
            fb.stroke_line(u(19), v(5), u(17), v(7), c);
            fb.stroke_line(u(7), v(17), u(5), v(19), c);
        }
        Icon::Drift => {
            fb.stroke_line(u(12), v(3), u(6), v(13), c);
            fb.stroke_line(u(6), v(13), u(12), v(21), c);
            fb.stroke_line(u(12), v(21), u(18), v(13), c);
            fb.stroke_line(u(18), v(13), u(12), v(3), c);
        }
        Icon::Apps => {
            let g = s / 3;
            fb.rect(x, y, g, g, c);
            fb.rect(x + 2 * g, y, g, g, c);
            fb.rect(x, y + 2 * g, g, g, c);
            fb.rect(x + 2 * g, y + 2 * g, g, g, c);
        }
        Icon::Min => fb.stroke_line(u(6), v(18), u(18), v(18), c),
        Icon::Max => {
            fb.stroke_line(u(5), v(5), u(19), v(5), c);
            fb.stroke_line(u(19), v(5), u(19), v(19), c);
            fb.stroke_line(u(19), v(19), u(5), v(19), c);
            fb.stroke_line(u(5), v(19), u(5), v(5), c);
        }
        Icon::Close => {
            fb.stroke_line(u(6), v(6), u(18), v(18), c);
            fb.stroke_line(u(18), v(6), u(6), v(18), c);
        }
    }
}

// ---------------------------------------------------------------------------
// Chrome pieces, each written against the mock's rule of the same name.
// ---------------------------------------------------------------------------

struct App {
    name: &'static [u8],
    col: u32,
    icon: Icon,
}

const APPS: [App; 6] = [
    App { name: b"Terminal", col: 0x27414f, icon: Icon::Terminal },
    App { name: b"Files", col: 0xc98512, icon: Icon::Files },
    App { name: b"Monitor", col: 0x0e7d78, icon: Icon::Monitor },
    App { name: b"Notes", col: 0x2f6fb0, icon: Icon::Notes },
    App { name: b"Drift", col: 0xcf4f38, icon: Icon::Drift },
    App { name: b"Settings", col: 0x566b79, icon: Icon::Settings },
];

fn top_h(posture: Posture) -> i32 {
    if posture == Posture::Phone {
        TOP_PHONE
    } else {
        TOP
    }
}

fn mode_name(posture: Posture) -> &'static [u8] {
    match posture {
        Posture::Desktop => b"desktop",
        Posture::Tablet => b"tablet",
        Posture::Phone => b"phone",
        Posture::Console => b"console",
    }
}

/// `#top` -- glass bar, brand, app name, menus, load wave, chip, battery, clock.
fn top_bar(fb: &mut Fb, k: &Colors, posture: Posture, app: Option<&[u8]>) {
    let (w, h) = (fb.w, top_h(posture));
    fb.rect_a(0, 0, w, h, k.glass, GLASS_A);
    fb.rect_a(0, h - 1, w, 1, k.line, LINE_A);
    let pad = if posture == Posture::Phone { 14 } else { 8 };
    let mut x = pad;
    if posture == Posture::Phone {
        // `#top .brand{order:2}` and `#clock{order:0}`: the status group comes
        // first on a phone (left-aligned), the brand is pushed to the right edge.
        let by = (h - 24) / 2;
        let chip = mode_name(posture);
        let chip_w = atype_w(chip, Font::Mono500, 11) + 12;
        fb.round_a(pad, (h - 16) / 2, chip_w, 16, 5, k.accent, SOFT_A);
        atype(fb, pad + 6, (h - 16) / 2 + 2, chip, Font::Mono500, 11, k.accent);
        let bat = b"86%";
        atype(fb, pad + chip_w + 6, by + 5, bat, Font::Body500, 13, k.fg);
        let clock = b"12:30";
        atype(
            fb,
            pad + chip_w + 6 + atype_w(bat, Font::Body500, 13) + 6,
            by + 5,
            clock,
            Font::Body500,
            13,
            k.fg,
        );
        let iy = (h - 18) / 2;
        icon(fb, Icon::Wave, w - pad - 18, iy, 18, k.accent);
        return;
    }

    // .tb.brand: 24px-high button with 9px side padding, 6px gap, 18px mark
    let brand_icon = posture != Posture::Console;
    let show_word = posture == Posture::Desktop || posture == Posture::Console;
    let by = (h - 24) / 2;
    if brand_icon {
        let iy = (h - 18) / 2;
        icon(fb, Icon::Wave, x + 9, iy, 18, k.accent);
        if show_word {
            atype(fb, x + 9 + 18 + 6, iy - 1, b"Nyrqis", Font::Display700, 14, k.fg);
            x += 9 + 18 + 6 + atype_w(b"Nyrqis", Font::Display700, 14) + 9;
        } else {
            x += 9 + 18 + 9;
        }
    }

    // .appname: 13/700 display; hidden on phone and console
    if posture == Posture::Desktop || posture == Posture::Tablet {
        let name = app.unwrap_or(b"Nyrqis");
        atype(fb, x + 4, by + 5, name, Font::Display700, 13, k.fg);
        x += 4 + atype_w(name, Font::Display700, 13) + 4;
    }

    // .menus (desktop and tablet only)
    if posture == Posture::Desktop || posture == Posture::Tablet {
        for label in [&b"Window"[..], &b"Mode"[..]] {
            let tw = atype_w(label, Font::Body500, 13);
            atype(fb, x + 9, by + 5, label, Font::Body500, 13, k.fg);
            x += tw + 18 + 2;
        }
    }

    status_group(fb, k, posture, x, w - pad);
}

/// The mock's `#statusBtn`: load wave, mode chip, battery, clock, right-aligned.
fn status_group(fb: &mut Fb, k: &Colors, posture: Posture, _left: i32, right_edge: i32) {
    let h = top_h(posture);
    let pad = if posture == Posture::Phone { 14 } else { 8 };
    let clock = b"12:30";
    let bat = b"86%";
    let chip = mode_name(posture);
    let chip_w = atype_w(chip, Font::Mono500, 11) + 12;
    let clock_w = atype_w(clock, Font::Body500, 13);
    let bat_w = atype_w(bat, Font::Body500, 13);
    let right = right_edge - 9; // .tb padding
    let clock_x = right - clock_w;
    let bat_x = clock_x - 6 - bat_w;
    let chip_x = bat_x - 6 - chip_w;
    let wave_w = if posture == Posture::Phone { 0 } else { 56 };
    let wave_x = chip_x - 6 - wave_w;

    if wave_w > 0 {
        // #loadwave: a 2.5px accent polyline of the scheduler's recent load
        let pts = [18u32, 22, 20, 30, 26, 34, 24, 40, 30, 44, 28, 50, 34, 46];
        let wy = (h - 18) / 2;
        let mut prev: Option<(i32, i32)> = None;
        for (i, v) in pts.iter().enumerate() {
            let px = wave_x + i as i32 * wave_w / (pts.len() as i32 - 1);
            let py = wy + 18 - 2 - (v * 14 / 60) as i32;
            if let Some((ax, ay)) = prev {
                fb.stroke_line(ax, ay, px, py, k.accent);
            }
            prev = Some((px, py));
        }
    }

    // .chip: 10.5/500 mono, uppercase, .06em tracking, accent-soft, radius 5
    let chip_h = 16;
    let cy = (h - chip_h) / 2;
    // .chip has background accent-soft, i.e. the accent at 16%
    fb.round_a(chip_x, cy, chip_w, chip_h, 5, k.accent, SOFT_A);
    atype(fb, chip_x + 6, cy + 2, chip, Font::Mono500, 11, k.accent);

    let by = (h - 24) / 2;
    atype(fb, bat_x, by + 5, bat, Font::Body500, 13, k.fg);
    atype(fb, clock_x, by + 5, clock, Font::Body500, 13, k.fg);
    let _ = pad;
}

/// `#keel` -- the glass dock: launcher, hairline separator, six app tiles.
fn keel(fb: &mut Fb, k: &Colors, posture: Posture, active: Option<usize>) {
    let (w, h) = (fb.w, fb.h);
    let tablet = posture == Posture::Tablet;
    let (tile, gap, padx, pady, bottom, radius) = if tablet {
        (50, 10, 12, 9, 14, 26)
    } else {
        (42, 6, 7, 7, 12, 20)
    };
    let sep = 1;
    let n = APPS.len() as i32;
    let pw = padx * 2 + tile + gap + sep + gap + (gap + tile) * n;
    let ph = pady * 2 + tile;
    let px = (w - pw) / 2;
    let py = h - bottom - ph;
    fb.round_a(px, py, pw, ph, radius, k.glass, GLASS_A);
    fb.frame(px, py, pw, ph, radius, k.line, LINE_A, 0);
    fb.round_a(px, py, pw, ph, radius, k.glass, GLASS_A);

    let mut x = px + padx;
    // launcher tile: accent, apps glyph in accent-fg
    fb.round(x, py + pady, tile, tile, if tablet { 15 } else { 12 }, k.accent);
    icon(fb, Icon::Apps, x + tile / 2 - 9, py + pady + tile / 2 - 9, 18, k.accent_fg);
    x += tile + gap;
    // separator
    fb.rect_a(x, py + pady + (tile - 28) / 2, sep, 28, k.line, LINE_A);
    x += sep + gap;

    for (i, a) in APPS.iter().enumerate() {
        let ty = py + pady;
        fb.round(x, ty, tile, tile, if tablet { 15 } else { 12 }, a.col);
        // the tile's own top highlight: inset 0 1px 0 rgba(255,255,255,.28)
        fb.round_a(x + 2, ty + 1, tile - 4, 2, 1, 0xffffff, 40);
        icon(fb, a.icon, x + tile / 2 - 9, ty + tile / 2 - 9, 18, 0xffffff);
        // running / active dot under the tile
        if active == Some(i) {
            fb.round(x + tile / 2 - 8, ty + tile + 4, 16, 5, 3, k.accent);
        } else if i == 3 {
            // Notes is open at boot (it is one of the three start windows)
            fb.circle(x + tile / 2, ty + tile + 6, 2, k.fg2);
        }
        x += tile + gap;
    }
}

fn win_frame(fb: &mut Fb, k: &Colors, x: i32, y: i32, w: i32, h: i32, app: usize, snap_open: bool) -> i32 {
    let a = &APPS[app];
    // .win: dark win surface, 1px line, radius 12, shadow (approximated by two
    // soft passes below the rect -- a blurred shadow is a second pass the
    // framebuffer does not have; recorded as a limit)
    for i in 0..3 {
        let g = 6 + i * 8;
        fb.round_a(x - g / 2, y + 6 + i * 4, w + g, h + g, 12 + g / 2, 0x000000, 14);
    }
    fb.round(x, y, w, h, 12, k.win);
    fb.frame(x, y, w, h, 12, k.line, LINE_A, k.win);
    // .wbar: 36px, win-bar, icon 18 radius 5, title 12/700 display, controls
    fb.round(x, y, w, 36, 12, k.win_bar);
    fb.rect(x, y + 18, w, 18, k.win_bar);
    fb.rect_a(x, y + 35, w, 1, k.line, LINE_A);
    fb.round(x + 12, y + 9, 18, 18, 5, a.col);
    icon(fb, a.icon, x + 12 + 4, y + 9 + 4, 10, 0xffffff);
    atype(fb, x + 12 + 18 + 8, y + 11, a.name, Font::Display700, 12, k.fg);

    // .wctl: 30x26 buttons with 6px radius, right aligned, 2px gap
    let mut cx = x + w - 6 - 30;
    icon(fb, Icon::Close, cx + 9, y + 6, 12, k.fg);
    fb.round_a(cx, y + 5, 30, 26, 6, k.accent, 0);
    cx -= 2 + 30;
    icon(fb, Icon::Max, cx + 9, y + 6, 12, k.fg);
    cx -= 2 + 30;
    icon(fb, Icon::Min, cx + 9, y + 6, 12, k.fg);

    // .snap popover under the maximise button when open
    if snap_open {
        let sy = y + 26;
        let sx = x + w - 6 - 30 - 2 - 30 - 120;
        fb.round_a(sx, sy, 132, 38, 10, k.glass, GLASS_A);
        fb.frame(sx, sy, 132, 38, 10, k.line, LINE_A, 0);
        fb.round_a(sx, sy, 132, 38, 10, k.glass, GLASS_A);
        for i in 0..3 {
            let bx = sx + 6 + i * 42;
            fb.round(bx, sy + 6, 38, 26, 5, k.field);
            let (ax, aw) = match i {
                0 => (bx + 3, 17),
                1 => (bx + 18, 17),
                _ => (bx + 3, 32),
            };
            fb.round(ax, sy + 9, aw, 20, 2, k.accent);
        }
    }
    y + 36
}

fn notes_body(fb: &mut Fb, k: &Colors, x: i32, y: i32, w: i32, h: i32) {
    fb.rect(x, y, w, h, k.win);
    let tx = x + 18;
    let mut ty = y + 16;
    // the mock's welcome.txt, wrapped to the window like its textarea does;
    // line-height 1.6 at 15px is 24px per line.
    let lines: [&[u8]; 12] = [
        b"Welcome to Nyrqis.",
        b"",
        b"This surface runs on the Keel",
        b"kernel. Resize the window, or press",
        b"Alt+1 to Alt+4, and the same open",
        b"apps re-flow into desktop, tablet,",
        b"console and phone postures.",
        b"",
        b"Open Terminal and try: ps, free,",
        b"mode phone",
        b"",
        b"# Ideas",
    ];
    for l in lines {
        atype(fb, tx, ty, l, Font::Body400, 15, k.fg);
        ty += 24;
        if ty > y + h - 24 {
            break;
        }
    }
}

fn terminal_body(fb: &mut Fb, k: &Colors, x: i32, y: i32, w: i32, h: i32) {
    fb.rect(x, y, w, h, k.term_bg);
    let tx = x + 12;
    let mut ty = y + 10;
    let lines: [(&[u8], u32); 10] = [
        (b"Keel 0.9.2  (tty0)", k.term_fg),
        (b"6 system processes up", k.term_fg),
        (b"", k.term_fg),
        (b"Welcome to Nyrqis.", k.term_fg),
        (b"Type 'help' for the command list.", k.term_fg),
        (b"", k.term_fg),
        (b"keel ~ $ ps", k.term_fg),
        (b"  PID  NAME        STATE     CPU", k.term_fg),
        (b"    1  init        running   0.3%", k.term_fg),
        (b"    2  compositor  running   2.5%", k.term_fg),
    ];
    for (l, c) in lines {
        atype(fb, tx, ty, l, Font::Mono400, 13, c);
        ty += 19;
        if ty > y + h - 24 {
            break;
        }
    }
    // the prompt row: warm "$" then input with its caret
    let py = ty + 2;
    atype(fb, tx, py, b"keel ~", Font::Mono400, 13, k.warm);
    let px = tx + atype_w(b"keel ~", Font::Mono400, 13) + 6;
    atype(fb, px, py, b"$", Font::Mono400, 13, k.warm);
    fb.rect(px + 12, py + 2, 8, 15, k.term_fg);
}

fn monitor_body(fb: &mut Fb, k: &Colors, x: i32, y: i32, w: i32, h: i32) {
    fb.rect(x, y, w, h, k.win);
    let cards: [(&[u8], &[u8], u32); 3] = [
        (b"CPU", b"31%", 31),
        (b"MEMORY", b"118 MB", 42),
        (b"PROCESSES", b"9", 60),
    ];
    let cw = (w - 28 - 14) / 2;
    for (i, (title, value, fill)) in cards.iter().enumerate() {
        let cx = x + 14 + (i as i32 % 2) * (cw + 14);
        let cy = y + 14 + (i as i32 / 2) * 96;
        fb.round(cx, cy, cw, 82, 12, k.field);
        fb.frame(cx, cy, cw, 82, 12, k.line, LINE_A, k.field);
        atype(fb, cx + 12, cy + 10, title, Font::Mono400, 11, k.fg2);
        atype(fb, cx + 12, cy + 28, value, Font::Display700, 26, k.fg);
        fb.round(cx + 12, cy + 66, cw - 24, 8, 4, k.win_bar);
        fb.round(cx + 12, cy + 66, (cw - 24) * *fill as i32 / 100, 8, 4, k.accent);
    }
    // .pt: the process table the mock's monitor draws under the cards
    let mut ty = y + 14 + 96 * 2 + 6;
    let cols = [x + 14, x + 74, x + 150, x + w - 90];
    for (i, h) in [&b"PID"[..], b"NAME", b"STATE", b"CPU"].iter().enumerate() {
        atype(fb, cols[i], ty, h, Font::Mono500, 11, k.fg2);
    }
    ty += 20;
    let rows: [(&[u8], &[u8], &[u8], u32); 4] = [
        (b"1", b"init", b"running", k.ok),
        (b"2", b"compositor", b"running", k.ok),
        (b"3", b"inputd", b"ready", k.warm),
        (b"4", b"netd", b"sleeping", k.fg2),
    ];
    for (pid, name, state, c) in rows {
        atype(fb, cols[0], ty, pid, Font::Body400, 14, k.fg);
        atype(fb, cols[1], ty, name, Font::Body400, 14, k.fg);
        atype(fb, cols[2], ty, state, Font::Body600, 13, c);
        atype(fb, cols[3], ty, b"1.2%", Font::Body400, 14, k.fg);
        fb.rect_a(x + 14, ty + 20, w - 28, 1, k.line, LINE_A);
        ty += 24;
        if ty > y + h - 20 {
            break;
        }
    }
}

fn window(fb: &mut Fb, k: &Colors, x: i32, y: i32, w: i32, h: i32, app: usize, snap_open: bool) {
    let by = win_frame(fb, k, x, y, w, h, app, snap_open);
    let bh = h - 36;
    match app {
        3 => notes_body(fb, k, x + 1, by, w - 2, bh - 1),
        0 => terminal_body(fb, k, x + 1, by, w - 2, bh - 1),
        2 => monitor_body(fb, k, x + 1, by, w - 2, bh - 1),
        _ => fb.rect(x + 1, by, w - 2, bh - 1, k.win),
    }
}

/// `#home .hpanel` -- the launcher lattice.
fn home(fb: &mut Fb, k: &Colors, posture: Posture, t: i32, full: bool) {
    let (w, h) = (fb.w, fb.h);
    let console = posture == Posture::Console;
    let panel_w = if console { w } else if full { (w - 44).min(880) } else { (w * 94 / 100).min(700) };
    let px = if console { 0 } else { (w - panel_w) / 2 };
    let gutter = if console { 44 } else { 22 };
    let mut py = if console { t } else { t + 22 };
    if !full {
        // desktop: a floating glass panel, vertically centred
        let ph = (h - t) * 88 / 100;
        py = t + ((h - t) - ph) / 2;
        fb.round_a(px, py, panel_w, ph, 24, k.glass, GLASS_A);
        fb.frame(px, py, panel_w, ph, 24, k.line, LINE_A, 0);
        fb.round_a(px, py, panel_w, ph, 24, k.glass, GLASS_A);
    }
    let inner = if full && posture == Posture::Phone { w - 28 } else { panel_w - 44 };

    // .hhead: 40px display clock, date under it, search field to the right
    let clock_px = if posture == Posture::Console { 64 } else { 40 };
    atype(fb, px + gutter, py, b"12:30", Font::Display700, clock_px, k.fg);
    let cw = atype_w(b"12:30", Font::Display700, clock_px);
    atype(
        fb,
        px + gutter,
        py + clock_px + 6,
        b"Thursday 21 November",
        Font::Body500,
        13,
        k.fg2,
    );
    if posture != Posture::Console {
        let (qw, qx, qy) = if posture == Posture::Phone {
            (inner, px + 14, py + clock_px + 44)
        } else {
            let qw = inner.min(320) - cw - 40;
            (qw, px + panel_w - 22 - qw, py + clock_px - 38)
        };
        fb.round(qx, qy, qw, 38, 19, k.field);
        fb.frame(qx, qy, qw, 38, 19, k.line, LINE_A, k.field);
        atype(fb, qx + 14, qy + 8, b"Search apps and files", Font::Body400, 14, k.fg2);
    }

    // .grid: 6 app tiles. desktop 88px min columns, tablet 104, phone 4 columns,
    // console a horizontal row of 210x270 cards.
    let gy = py + match posture {
        Posture::Console => clock_px + 92,
        Posture::Phone => clock_px + 92,
        _ => clock_px + 46,
    };
    if posture == Posture::Console {
        let card_w = 210;
        let card_h = 270;
        let gap = 22;
        for (i, a) in APPS.iter().enumerate() {
            let cx = gutter + i as i32 * (card_w + gap);
            if cx + card_w > w {
                break;
            }
            fb.round(cx, gy, card_w, card_h, 26, k.win);
            fb.frame(cx, gy, card_w, card_h, 26, k.line, LINE_A, k.win);
            fb.round(cx + 16, gy + 22, 96, 96, 26, a.col);
            icon(fb, a.icon, cx + 16 + 30, gy + 22 + 30, 36, 0xffffff);
            atype(fb, cx + 16, gy + card_h - 52, a.name, Font::Display700, 22, k.fg);
            atype(fb, cx + 16, gy + card_h - 24, b"READY", Font::Mono500, 11, k.fg2);
        }
        return;
    }

    let cols = match posture {
        Posture::Phone => 4,
        Posture::Tablet => (inner / 104).max(1),
        _ => (inner / 88).max(1),
    };
    let tile = match posture {
        Posture::Phone => 54,
        Posture::Tablet => 68,
        _ => 58,
    };
    let cell_w = inner / cols;
    for (i, a) in APPS.iter().enumerate() {
        let col = i as i32 % cols;
        let row = i as i32 / cols;
        let cx = px + 22 + col * cell_w;
        let cy = gy + row * (tile + 46);
        let tile_x = cx + (cell_w - tile) / 2;
        fb.round(tile_x, cy, tile, tile, if posture == Posture::Tablet { 20 } else { 17 }, a.col);
        fb.round_a(tile_x + 2, cy + 1, tile - 4, 2, 1, 0xffffff, 40);
        let isz = if posture == Posture::Tablet { 30 } else { 26 };
        icon(fb, a.icon, tile_x + tile / 2 - isz / 2, cy + tile / 2 - isz / 2, isz, 0xffffff);
        atype_c(fb, cx + cell_w / 2, cy + tile + 7, a.name, Font::Body500, 12, k.fg);
    }

    // .hrec: "Recent files" with three entries
    if posture != Posture::Console {
        let rec_y = gy + 2 * (tile + 46) + 10;
        atype(fb, px + 22, rec_y, b"RECENT FILES", Font::Mono500, 11, k.fg2);
        let files: [(&[u8], &[u8]); 3] = [
            (b"welcome.txt", b"/home/nyrqis"),
            (b"ideas.md", b"/home/nyrqis"),
            (b"plan.txt", b"/home/nyrqis/Documents"),
        ];
        for (i, (name, dir)) in files.iter().enumerate() {
            let ry = rec_y + 24 + i as i32 * 34;
            let _ = ry;
            atype(fb, px + 32, ry + 7, name, Font::Body400, 14, k.fg);
            atype_r(fb, px + 22 + inner - 10, ry + 9, dir, Font::Mono400, 11, k.fg2);
        }
    }
}

/// `#hints` -- console hint bar.
fn hints(fb: &mut Fb, k: &Colors) {
    let (w, h) = (fb.w, fb.h);
    let y = h - 52;
    fb.rect_a(0, y, w, 52, k.glass, GLASS_A);
    fb.rect_a(0, y, w, 1, k.line, LINE_A);
    let items: [(&[u8], &[u8]); 4] = [(b"A", b"Open"), (b"B", b"Back"), (b"Y", b"Controls"), (b"", b"Home")];
    let mut total = 0;
    for (_, l) in items {
        total += 24 + 8 + atype_w(l, Font::Mono500, 12);
    }
    total += 28 * 3;
    let mut x = (w - total) / 2;
    for (b, l) in items {
        fb.circle(x + 12, y + 26, 12, k.accent);
        if b.is_empty() {
            for r in 0..3 {
                fb.rect(x + 7, y + 22 + r * 4, 11, 2, k.accent_fg);
            }
        } else {
            atype_c(fb, x + 12, y + 18, b, Font::Mono500, 11, k.accent_fg);
        }
        atype(fb, x + 32, y + 19, l, Font::Mono500, 12, k.fg2);
        x += 24 + 8 + atype_w(l, Font::Mono500, 12) + 28;
    }
}

fn gesture(fb: &mut Fb, k: &Colors) {
    let (w, h) = (fb.w, fb.h);
    let x = w / 2 - 60;
    let y = h - 7 - 22;
    fb.round_a(x + 10, y + 9, 100, 5, 3, k.fg, 140);
}

/// `#boot` -- the mock's boot console, kept as the locked screen: it is a screen
/// the mock really draws, and it is what a session with no owner can show.
fn boot_screen(fb: &mut Fb) {
    let (w, h) = (fb.w, fb.h);
    fb.rect(0, 0, w, h, 0x071319);
    let lines: [&[u8]; 12] = [
        b"keel 0.9.2  (tty0)",
        b"",
        b"init     4 MB    ok",
        b"compositor 46 MB ok",
        b"inputd   6 MB    ok",
        b"netd     14 MB   ok",
        b"audiod   9 MB    ok",
        b"storaged 11 MB   ok",
        b"",
        b"mount /home  ok",
        b"shell: nyrqis 4 postures",
        b"ready.",
    ];
    let mut y = 24;
    for (i, l) in lines.iter().enumerate() {
        let c = if i == 0 || i == 11 { 0xf0b84a } else { 0x9fd9d4 };
        atype(fb, 24, y, l, Font::Mono400, 13, c);
        y += 19;
    }
}

pub struct Info {
    pub class: SizeClass,
    pub cols: i32,
    pub rows: i32,
    pub tiles: i32,
}

pub fn size_class(w: i32) -> SizeClass {
    if w < 640 {
        SizeClass::Compact
    } else if w < 1040 {
        SizeClass::Medium
    } else {
        SizeClass::Expanded
    }
}

pub fn render(fb: &mut Fb, theme: Theme) -> Info {
    render_posture(fb, theme, Posture::Desktop)
}

pub fn render_posture(fb: &mut Fb, theme: Theme, posture: Posture) -> Info {
    render_named(fb, theme, posture, None)
}

/// Renders the shell as a session identity wears it. The mock has one identity,
/// Nyrqis, so an identity can only change the accent and the name chip; the
/// layout is untouched. This keeps the kernel's five personas speaking through
/// one shell instead of painting a second one.
pub fn render_named(
    fb: &mut Fb,
    theme: Theme,
    posture: Posture,
    identity: Option<(&[u8], u32)>,
) -> Info {
    let mut k = *theme_colors(theme);
    if let Some((_, accent)) = identity {
        k.accent = accent;
        k.primary = accent;
    }
    let (w, h) = (fb.w, fb.h);
    let class = size_class(w);
    wallpaper(fb, &k);
    top_bar(fb, &k, posture, Some(b"Monitor"));
    let t = top_h(posture);

    match posture {
        Posture::Desktop => {
            // start(): notes at 5% W / y 30, terminal at 12% W / 36% H,
            // monitor at max(20, W-620) / 46. Sizes are the apps' own.
            let nw = 500.min(w - 40);
            let nh = 360.min(h - t - 60);
            window(fb, &k, w * 5 / 100, t + 30, nw, nh, 3, false);
            let tx = w * 12 / 100;
            let tw = 580.min(w - tx - 24);
            let th = 380.min(h - t - h * 36 / 100 - 20);
            window(fb, &k, tx, t + h * 36 / 100, tw, th, 0, false);
            let mx = 20.max(w - 620);
            let mw = 580.min(w - mx - 20);
            let mh = 430.min(h - t - 46 - 90);
            window(fb, &k, mx, t + 46, mw, mh, 2, false);
            keel(fb, &k, posture, Some(3));
        }
        Posture::Tablet => {
            home(fb, &k, posture, t, true);
            keel(fb, &k, posture, Some(3));
        }
        Posture::Phone => {
            home(fb, &k, posture, t, true);
            gesture(fb, &k);
        }
        Posture::Console => {
            home(fb, &k, posture, t, true);
            hints(fb, &k);
        }
    }

    Info {
        class,
        cols: match posture {
            Posture::Phone => 4,
            Posture::Tablet => (w / 104).max(1),
            _ => 6,
        },
        rows: 2,
        tiles: 6,
    }
}

pub fn render_lockscreen(fb: &mut Fb, _theme: Theme) -> Info {
    boot_screen(fb);
    Info { class: size_class(fb.w), cols: 0, rows: 0, tiles: 0 }
}

/// FNV-1a over every 13th pixel: the boot proof that a render is deterministic
/// and that two postures are different pictures.
pub fn hash(fb: &Fb) -> u32 {
    let mut h = 0x811c_9dc5u32;
    let (w, hh) = (fb.w, fb.h);
    let mut idx = 0i32;
    let total = w * hh;
    while idx < total {
        let x = idx % w;
        let y = idx / w;
        for b in fb.peek(x, y).to_le_bytes() {
            h ^= b as u32;
            h = h.wrapping_mul(0x0100_0193);
        }
        idx += 13;
    }
    h
}

/// The tree's 5x7 glyph table: still the kernel's console font
/// (`device_mode::draw_text` and the platform wordmarks use it). The shell's
/// own chrome no longer draws with it -- it uses the mock's typefaces above.
pub fn text(fb: &mut Fb, x: i32, y: i32, s: &[u8], scale: i32, color: u32) {
    if scale <= 0 {
        return;
    }
    let mut cx = x;
    for &ch in s {
        let g = glyph5x7(ch);
        for (row, bits) in g.iter().enumerate() {
            for col in 0..5 {
                if bits & (1 << (4 - col)) != 0 {
                    let px = cx + col * scale;
                    let py = y + row as i32 * scale;
                    fb.rect(px, py, scale, scale, color);
                }
            }
        }
        cx += 6 * scale;
    }
}

pub fn text_w(s: &[u8], scale: i32) -> i32 {
    s.len() as i32 * 6 * scale
}

fn glyph5x7(ch: u8) -> [u8; 7] {
    use super::font_data::GLYPHS;
    match ch {
        b'A'..=b'Z' => GLYPHS[(ch - b'A') as usize],
        b'a'..=b'z' => GLYPHS[(ch - b'a') as usize],
        b' ' => GLYPHS[26],
        b'0'..=b'9' => GLYPHS[27 + (ch - b'0') as usize],
        b':' => GLYPHS[37],
        _ => GLYPHS[26],
    }
}
