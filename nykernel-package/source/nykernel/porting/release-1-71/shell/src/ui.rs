//! ui.rs -- the Nyrqis shell, drawn in the **Tide** design language: one compositor, four postures.
//!
//! Source of truth: the Tide Shell mock (`Tide Shell.html`) the project adopted. What was ported is the DESIGN, not the DOM: the
//! paper/glass palette with a single teal accent, the 34 px top bar (brand, posture chip, battery, clock), the keel dock (launcher
//! tile, app tiles, running/active dots), the launcher lattice (big clock, date, search field, app grid, recent files), the window
//! chrome (icon tile, title, minimise / maximise / close and the snap popover), the control centre (2x2 quick tiles, brightness and
//! volume sliders, notification list), the console posture's horizontal tile row with its hint bar, and the phone posture's
//! full-bleed grid with its gesture pill.
//!
//! What could not be ported is the type: Tide specifies Bricolage Grotesque / IBM Plex Sans / IBM Plex Mono, and this tree has ONE
//! face -- the 5x7 bitmap generated from tools/font5x7.txt and shared with the display service and the console. The design is drawn
//! in that face at two sizes, so the layout, colour and spacing are Tide's and the letterforms are the kernel's.
//!
//! Palette: Tide's own tokens, flattened. Browsers can blend `rgba()` glass over a wallpaper; this renderer cannot, so each glass
//! surface is the pre-blended opaque token (documented per constant below). That is the one place the design is approximated rather
//! than copied, and it is deliberate: a per-pixel blend would cost more than it says on a still frame.
//!
//! `render()` is pure drawing into a caller-supplied buffer: the kernel's framebuffer driver and the host tool
//! (`porting/release-1-71/shell/host/hostrender.rs`) run the SAME source, so a screen can be reviewed before it is booted.
//! Units: 1 dp = 1 px. No allocation, no floats, no statics.
use crate::font_data::GLYPHS;

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Theme { Light, Dark }

/// The four Tide postures. They are not themes: each one is a different arrangement of the same elements (dock size, window or
/// lattice, hint bar), which is how one shell answers four machines.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Posture { Desktop, Tablet, Phone, Console }

/// Tide's palette under the kernel's existing role names. The mapping is the design's, one token to one role:
///   surface = --paper, container = --glass, container_high = --win, container_highest = --win-bar,
///   primary = --accent, primary_container = --accent-soft, on_primary = --accent-fg, outline_variant = --line,
///   on_surface_variant = --fg2, secondary_container = --ok, tertiary_container = --danger.
#[derive(Clone, Copy)]
pub struct Colors {
    pub surface: u32, pub on_surface: u32, pub on_surface_variant: u32,
    pub container: u32, pub container_high: u32, pub container_highest: u32,
    pub primary: u32, pub on_primary: u32, pub primary_container: u32, pub on_primary_container: u32,
    pub secondary_container: u32, pub on_secondary_container: u32, pub tertiary_container: u32, pub on_tertiary_container: u32,
    pub outline_variant: u32,
}
pub const LIGHT: Colors = Colors {
    surface: 0xE8EFF1, on_surface: 0x10242E, on_surface_variant: 0x53697A,
    container: 0xF7FBFB, container_high: 0xF8FBFC, container_highest: 0xE1EAED,
    primary: 0x0B8F8C, on_primary: 0xFFFFFF, primary_container: 0xE2F2F1, on_primary_container: 0x084C4A,
    secondary_container: 0x2E9D62, on_secondary_container: 0xFFFFFF, tertiary_container: 0xD4493B, on_tertiary_container: 0xFFFFFF,
    outline_variant: 0xD3DDE1,
};
pub const DARK: Colors = Colors {
    surface: 0x0B171E, on_surface: 0xE4EFF3, on_surface_variant: 0x8FA8B5,
    container: 0x101F27, container_high: 0x13252E, container_highest: 0x1A323D,
    primary: 0x3CC8C0, on_primary: 0x052321, primary_container: 0x123A38, on_primary_container: 0xCDEFEC,
    secondary_container: 0x52C98A, on_secondary_container: 0x06210F, tertiary_container: 0xEF7A6C, on_tertiary_container: 0x230805,
    outline_variant: 0x2B444F,
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
    /// Rounded rectangle, corner radius r (clamped to min(w,h)/2). A pixel (px,py) of a corner is inside when its CENTRE is within the
    /// circle of radius r: (2px+1-2r)^2 + (2py+1-2r)^2 <= 4r^2 (integer arithmetic, so every implementation agrees bit for bit).
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

/// A 1 dp hairline rounded rect: fill, then paint the inside back in `bg`. Tide's borders are `1px solid var(--line)` everywhere,
/// so this is the shell's most used primitive.
fn outline(fb: &mut Fb, x: i32, y: i32, w: i32, h: i32, r: i32, c: u32, bg: u32) {
    if w < 3 || h < 3 { return; }
    fb.round(x, y, w, h, r, c);
    fb.round(x + 1, y + 1, w - 2, h - 2, if r > 1 { r - 1 } else { 0 }, bg);
}

/// Tide's two type steps, in the tree's one face: 14 px body, 21 px display (the mock's 40-64 px headline type is out of reach of a
/// 5x7 bitmap without destroying the letterforms, so the display step is the largest that stays legible).
pub const BODY_SCALE: i32 = 2;
pub const DISPLAY_SCALE: i32 = 3;
pub const TOP: i32 = 34;

pub const APPS: [&[u8]; 8] = [b"TERMINAL", b"FILES", b"MONITOR", b"NOTES", b"DRIFT", b"SETTINGS", b"CAMERA", b"PC"];
/// Tide's mock keeps six app tiles in the keel; the launcher shows all eight.
const KEEL_APPS: usize = 6;
const RECENTS: [(&[u8], &[u8]); 3] = [(b"REPORT PDF", b"/HOME"), (b"PLAN TXT", b"/HOME/DOCS"), (b"IDEAS MD", b"/HOME/TIDE")];
const NOTES: [(&[u8], &[u8]); 3] = [(b"WELCOME TO TIDE", b"NOW"), (b"CONTROLLER READY", b"2M"), (b"BUILD PASSED", b"9M")];

/// One glyph, drawn from rectangles: Tide's `apps` icon is four squares. Every other "icon" in this file is the app's initial in the
/// tile it owns, which is what the dock's `.tile` shows in the mock when the SVG set is unavailable -- one visual language, no assets.
fn apps_glyph(fb: &mut Fb, x: i32, y: i32, s: i32, c: u32) {
    let g = s / 3;
    fb.round(x, y, g, g, 1, c);
    fb.round(x + 2 * g, y, g, g, 1, c);
    fb.round(x, y + 2 * g, g, g, 1, c);
    fb.round(x + 2 * g, y + 2 * g, g, g, 1, c);
}

/// The top bar: brand + posture chip + battery + clock, on `--glass` with a `--line` rule under it. The kernel draws no chrome of its
/// own any more -- this IS the chrome, which is what "adapt the kernel around the shell" means for this design.
fn top_bar(fb: &mut Fb, k: &Colors, w: i32, name: &[u8], badge: bool) {
    fb.rect(0, 0, w, TOP, k.container);
    fb.rect(0, TOP - 1, w, 1, k.outline_variant);
    fb.round(10, 9, 16, 16, 5, k.primary);
    apps_glyph(fb, 14, 13, 8, k.on_primary);
    text(fb, 34, 10, b"TIDE", BODY_SCALE, k.on_surface);
    // posture chip (the mock's #modechip): uppercase mono, accent-soft -- or, when
    // a session identity is present, a solid accent badge naming that identity
    // (the kernel's five personas all speak through this one chip now).
    let cw = text_w(name, BODY_SCALE) + 16;
    // Left of the battery+clock cluster, with 12 dp of air: the first version
    // measured from the screen's margin instead and ran the chip into the clock.
    let clock_x = w - 10 - 24 - 10 - text_w(b"12:30", BODY_SCALE);
    let cx = clock_x - 12 - cw;
    if cx > 100 {
        if badge {
            fb.round(cx, 8, cw, 18, 6, k.primary);
            text(fb, cx + 8, 11, name, BODY_SCALE, k.on_primary);
        } else {
            fb.round(cx, 8, cw, 18, 6, k.primary_container);
            text(fb, cx + 8, 11, name, BODY_SCALE, k.on_primary_container);
        }
    }
    // battery (86%) then the clock, right-aligned on the 10 dp margin
    let bx = w - 10 - 24;
    outline(fb, bx, 11, 22, 11, 3, k.on_surface_variant, k.container);
    fb.round(bx + 3, 13, 14, 7, 1, k.on_surface_variant);
    let ckw = text_w(b"12:30", BODY_SCALE);
    text(fb, bx - 10 - ckw, 10, b"12:30", BODY_SCALE, k.on_surface);
}

/// The keel dock: centred pill, launcher tile (accent) then the six app tiles with running / active dots. `tile` is the posture's
/// scale (desktop 42, tablet 50). Returns its rect so callers can place overlays above it.
fn keel(fb: &mut Fb, k: &Colors, w: i32, h: i32, tile: i32) -> (i32, i32, i32, i32) {
    let n = KEEL_APPS as i32;
    let gap = if tile > 44 { 10 } else { 6 };
    let pad = 8;
    let pw = n * tile + (n - 1) * gap + 2 * pad + (tile + gap);   // apps + separator slot + launcher
    let ph = tile + 2 * pad;
    let px = (w - pw) / 2;
    let py = h - 12 - ph;
    fb.round(px, py, pw, ph, ph / 2, k.container_highest);
    // launcher tile
    fb.round(px + pad, py + pad, tile, tile, tile / 4, k.primary);
    apps_glyph(fb, px + pad + tile / 4, py + pad + tile / 4, tile / 2, k.on_primary);
    // separator
    let sx = px + pad + tile + gap / 2;
    fb.rect(sx, py + pad + 6, 1, tile - 12, k.outline_variant);
    let mut i = 0;
    while i < KEEL_APPS as i32 {
        let x = px + pad + tile + gap + i * (tile + gap);
        fb.round(x, py + pad, tile, tile, tile / 4, k.container_high);
        text_c(fb, x + tile / 2, py + pad + (tile - 7 * BODY_SCALE) / 2, &APPS[i as usize][..1], BODY_SCALE, k.on_surface);
        // running dot under every app (the mock's `.kb.run::after`); the active one is an accent bar
        let active = i == 0;
        let dw = if active { 16 } else { 5 };
        fb.round(x + tile / 2 - dw / 2, py + ph - 4, dw, 3, 1, if active { k.primary } else { k.on_surface_variant });
        i += 1;
    }
    (px, py, pw, ph)
}

/// A quick-settings tile from the control centre: accent-soft when on, `--field` when off.
fn quick_tile(fb: &mut Fb, k: &Colors, x: i32, y: i32, w: i32, label: &[u8], sub: &[u8], on: bool) {
    let bg = if on { k.primary_container } else { k.container_highest };
    fb.round(x, y, w, 52, 14, bg);
    if on { outline(fb, x, y, w, 52, 14, k.primary, bg); }
    fb.round(x + 10, y + 14, 24, 24, 12, if on { k.primary } else { k.container_high });
    text_c(fb, x + 22, y + 20, &label[..1], BODY_SCALE, if on { k.on_primary } else { k.on_surface_variant });
    text(fb, x + 42, y + 12, label, BODY_SCALE, k.on_surface);
    text(fb, x + 42, y + 30, sub, BODY_SCALE, k.on_surface_variant);
}

/// A Tide slider: 24 px row, accent fill and thumb (the mock uses a native range input, whose track is the accent).
fn slider(fb: &mut Fb, k: &Colors, x: i32, y: i32, w: i32, pct: i32) {
    outline(fb, x, y + 10, w, 4, 2, k.outline_variant, k.container);
    let mut filled = w * pct / 100;
    if filled < 10 { filled = 10; } if filled > w - 10 { filled = w - 10; }
    fb.round(x + 1, y + 11, filled - 2, 2, 1, k.primary);
    fb.round(x + filled - 7, y + 5, 14, 14, 7, k.primary);
}

/// The control centre (Tide's status-cluster dropdown): 2x2 quick tiles, brightness and volume, then notifications.
fn control_center(fb: &mut Fb, k: &Colors, x: i32, y: i32, w: i32, h: i32) {
    fb.round(x, y, w, h, 20, k.container);
    outline(fb, x, y, w, h, 20, k.outline_variant, k.container);
    let half = (w - 28 - 8) / 2;
    // Subtitles are kept inside their tile: "TIDEWATER 5G" is 142 px of this face
    // in a 158 px tile, which overflowed into its neighbour before the boot.
    quick_tile(fb, k, x + 14, y + 14, half, b"WI-FI", b"TIDEWATER", true);
    quick_tile(fb, k, x + 22 + half, y + 14, half, b"CONSOLE", b"CONNECTED", true);
    quick_tile(fb, k, x + 14, y + 74, half, b"THEME", b"DARK", false);
    quick_tile(fb, k, x + 22 + half, y + 74, half, b"QUIET", b"OFF", false);
    // brightness then volume, with the mock's sun / note marks as glyph-free dots
    fb.round(x + 16, y + 146, 6, 6, 3, k.on_surface_variant);
    slider(fb, k, x + 30, y + 136, w - 60, 100);
    fb.round(x + 16, y + 178, 6, 6, 3, k.on_surface_variant);
    slider(fb, k, x + 30, y + 168, w - 60, 60);
    // notifications
    let mut ny = y + 204;
    if ny + 20 < y + h {
        text(fb, x + 16, ny, b"NOTIFICATIONS", BODY_SCALE, k.on_surface_variant);
        ny += 22;
    }
    let mut ni = 0;
    while ni < NOTES.len() && ny + 44 <= y + h - 12 {
        fb.round(x + 14, ny, w - 28, 38, 12, k.container_highest);
        text(fb, x + 26, ny + 6, NOTES[ni].0, BODY_SCALE, k.on_surface);
        text(fb, x + 26, ny + 22, NOTES[ni].1, BODY_SCALE, k.on_surface_variant);
        ny += 44;
        ni += 1;
    }
}

/// The launcher lattice (Tide's #home .hpanel): clock + date, search field, app grid, recent files.
fn launcher(fb: &mut Fb, k: &Colors, x: i32, y: i32, w: i32, h: i32, phone: bool) {
    fb.round(x, y, w, h, 24, k.container);
    outline(fb, x, y, w, h, 24, k.outline_variant, k.container);
    text(fb, x + 20, y + 20, b"12:30", DISPLAY_SCALE, k.on_surface);
    text(fb, x + 20, y + 20 + 7 * DISPLAY_SCALE + 8, b"THU 22 OCT", BODY_SCALE, k.on_surface_variant);
    let sw = if phone { w - 40 } else { (w - 60) / 2 };
    let sx = if phone { x + 20 } else { x + w - 20 - sw };
    let sy = if phone { y + 76 } else { y + 26 };
    outline(fb, sx, sy, sw, 34, 17, k.outline_variant, k.container_highest);
    text(fb, sx + 14, sy + 10, b"SEARCH APPS AND FILES", BODY_SCALE, k.on_surface_variant);
    // app grid: 4 columns, 58 dp accent-tile icons with the app's initial (the mock's `.ap .tile`)
    let cols = if w > 620 { 4 } else { 4 };
    let grid_y = sy + 34 + 22;
    let cell = (w - 40) / cols;
    let mut n = 0;
    while n < 8 {
        let cx = x + 20 + (n as i32 % cols) * cell;
        let cy = grid_y + (n as i32 / cols) * 88;
        if cy + 74 <= y + h - 8 {
            fb.round(cx + (cell - 58) / 2, cy, 58, 58, 17, k.container_high);
            text_c(fb, cx + cell / 2, cy + 16, &APPS[n as usize][..1], DISPLAY_SCALE, k.primary);
            text_c(fb, cx + cell / 2, cy + 64, APPS[n as usize], BODY_SCALE, k.on_surface);
        }
        n += 1;
    }
    // recent files
    let ry = grid_y + 2 * 88 + 6;
    if ry + 40 <= y + h {
        text(fb, x + 20, ry, b"RECENT FILES", BODY_SCALE, k.on_surface_variant);
        let mut r = 0;
        while r < RECENTS.len() && ry + 26 + r as i32 * 30 + 26 <= y + h {
            fb.rect(x + 20, ry + 26 + r as i32 * 30, w - 40, 1, k.outline_variant);
            text(fb, x + 20, ry + 32 + r as i32 * 30, RECENTS[r].0, BODY_SCALE, k.on_surface);
            let dw = text_w(RECENTS[r].1, BODY_SCALE);
            text(fb, x + w - 20 - dw, ry + 32 + r as i32 * 30, RECENTS[r].1, BODY_SCALE, k.on_surface_variant);
            r += 1;
        }
    }
}

/// A Tide window: 12 dp radius, shadowed frame, 36 dp title bar with icon tile, title, and OUR minimise / maximise / close. The snap
/// popover is drawn open under the maximise control, which is how the mock shows it (`:hover .snap`), and its three buttons are the
/// layouts it offers: left half, right half, fill.
fn window(fb: &mut Fb, k: &Colors, x: i32, y: i32, w: i32, h: i32, title: &[u8]) {
    fb.round(x + 2, y + 8, w, h, 14, k.container_highest);       // the shadow, as one flat offset block
    fb.round(x, y, w, h, 12, k.container_high);
    outline(fb, x, y, w, h, 12, k.outline_variant, k.container_high);
    fb.rect(x + 1, y + 1, w - 2, 35, k.container_highest);        // title bar fill
    fb.rect(x + 1, y + 36, w - 2, 1, k.outline_variant);
    fb.round(x + 10, y + 9, 18, 18, 5, k.primary);
    text_c(fb, x + 19, y + 13, &title[..1], BODY_SCALE, k.on_primary);
    text(fb, x + 34, y + 11, title, BODY_SCALE, k.on_surface);
    let mut cx = x + w - 10 - 3 * 30;
    fb.rect(cx + 9, y + 18, 12, 2, k.on_surface_variant);                       // minimise
    cx += 30;
    outline(fb, cx + 6, y + 12, 11, 11, 2, k.on_surface_variant, k.container_highest);  // maximise
    cx += 30;
    let mut d = 0;
    while d < 9 {                                                              // close
        fb.rect(cx + 7 + d, y + 13 + d, 2, 2, k.on_surface_variant);
        fb.rect(cx + 15 - d, y + 13 + d, 2, 2, k.on_surface_variant);
        d += 1;
    }
    // snap popover under the maximise control
    let px = x + w - 10 - 3 * 30 + 30 - 34;
    let py = y + 38;
    fb.round(px, py, 96, 38, 10, k.container);
    outline(fb, px, py, 96, 38, 10, k.outline_variant, k.container);
    let mut s = 0;
    while s < 3 {
        let bx = px + 6 + s * 30;
        outline(fb, bx, py + 8, 26, 22, 5, k.outline_variant, k.container_highest);
        match s {
            0 => fb.rect(bx + 4, py + 12, 8, 14, k.primary),
            1 => fb.rect(bx + 14, py + 12, 8, 14, k.primary),
            _ => fb.rect(bx + 4, py + 12, 18, 14, k.primary),
        }
        s += 1;
    }
    // body: the mock's Notes app, as the text a still frame can show
    text(fb, x + 18, y + 54, b"SPLIT VIEW THAT SURVIVES MODE CHANGES", BODY_SCALE, k.on_surface);
    text(fb, x + 18, y + 74, b"DOCK THAT BECOMES A GESTURE BAR", BODY_SCALE, k.on_surface_variant);
    text(fb, x + 18, y + 94, b"CONSOLE TILES DRIVEN BY THE D-PAD", BODY_SCALE, k.on_surface_variant);
    fb.rect(x + 18, y + 116, w - 36, 1, k.outline_variant);
    text(fb, x + 18, y + 126, b"1. BOOT", BODY_SCALE, k.on_surface_variant);
    text(fb, x + 18, y + 144, b"2. ADAPT", BODY_SCALE, k.on_surface_variant);
    text(fb, x + 18, y + 162, b"3. SHIP", BODY_SCALE, k.on_surface_variant);
}

/// Everything the layout decided (also returned so a gate can assert invariants on the real numbers).
pub struct Info { pub class: SizeClass, pub cols: i32, pub rows: i32, pub tiles: i32 }

/// The host tool's entry point: the desktop posture.
pub fn render(fb: &mut Fb, theme: Theme) -> Info { render_posture(fb, theme, Posture::Desktop) }

fn posture_label(posture: Posture) -> &'static [u8] {
    match posture {
        Posture::Desktop => b"DESKTOP",
        Posture::Tablet => b"TABLET",
        Posture::Phone => b"PHONE",
        Posture::Console => b"CONSOLE",
    }
}

pub fn render_posture(fb: &mut Fb, theme: Theme, posture: Posture) -> Info {
    render_named(fb, theme, posture, None)
}

/// The same posture, worn by a session identity: `name` replaces the posture label in the top bar's
/// chip (as a solid accent badge) and `accent` becomes the shell's one accent -- brand tile, launcher
/// tile, control-centre sliders, lock glyph. This is how `crate::platform_mode`'s five identities
/// reach the screen: Tide is the shell, the identity is the accent and the name, never a second chrome
/// painted over it.
pub fn render_named(fb: &mut Fb, theme: Theme, posture: Posture, identity: Option<(&[u8], u32)>) -> Info {
    let mut k = match theme { Theme::Light => LIGHT, Theme::Dark => DARK };
    if let Some((_, accent)) = identity {
        k.primary = accent;
    }
    let (w, h) = (fb.w, fb.h);
    let class = size_class(w);
    let phone = posture == Posture::Phone || class == SizeClass::Compact;
    fb.rect(0, 0, w, h, k.surface);
    let (label, badge) = match identity {
        Some((name, _)) => (name, true),
        None => (posture_label(posture), false),
    };
    top_bar(fb, &k, w, label, badge);
    match posture {
        Posture::Desktop => {
            let cw = if w >= 980 { 352 } else { 0 };
            let ww = w - 24 - 16 - cw - if cw > 0 { 12 } else { 0 };
            window(fb, &k, 24, TOP + 16, ww, h - TOP - 80, b"NOTES");
            if cw > 0 { control_center(fb, &k, w - 16 - cw, TOP + 12, cw, h - TOP - 76); }
            keel(fb, &k, w, h, 42);
        }
        Posture::Tablet => {
            let cw = if w >= 980 { 352 } else { 0 };
            let lw = w - 32 - cw - if cw > 0 { 12 } else { 0 };
            launcher(fb, &k, 16, TOP + 12, lw, h - TOP - 88, false);
            if cw > 0 { control_center(fb, &k, w - 16 - cw, TOP + 12, cw, h - TOP - 76); }
            keel(fb, &k, w, h, 50);
        }
        Posture::Phone => {
            launcher(fb, &k, 0, TOP, w, h - TOP - 22, phone);
            fb.round((w - 120) / 2, h - 14, 120, 5, 3, k.on_surface_variant);   // gesture pill
        }
        Posture::Console => {
            // Tide's console posture: a large clock, a horizontal row of app cards and a hint bar.
            text(fb, 44, TOP + 30, b"12:30", 6, k.on_surface);
            text(fb, 44, TOP + 84, b"THU 22 OCT", BODY_SCALE, k.on_surface_variant);
            let card_w = (w - 88 - 4 * 22) / 5;
            let mut n = 0;
            while n < 5 {
                let cx = 44 + n * (card_w + 22);
                fb.round(cx, TOP + 120, card_w, 190, 26, k.container_high);
                outline(fb, cx, TOP + 120, card_w, 190, 26, k.outline_variant, k.container_high);
                fb.round(cx + 18, TOP + 138, 72, 72, 22, k.container_highest);
                text_c(fb, cx + 54, TOP + 160, &APPS[n as usize][..1], DISPLAY_SCALE, k.primary);
                text(fb, cx + 18, TOP + 224, APPS[n as usize], BODY_SCALE, k.on_surface);
                text(fb, cx + 18, TOP + 246, b"READY", BODY_SCALE, k.on_surface_variant);
                n += 1;
            }
            fb.rect(0, h - 52, w, 52, k.container);
            fb.rect(0, h - 52, w, 1, k.outline_variant);
            let hints: [&[u8]; 4] = [b"A OPEN", b"B BACK", b"Y CONTROLS", b"HOME"];
            let mut hx = (w - 4 * 120) / 2;
            let mut i = 0;
            while i < 4 {
                fb.round(hx, h - 38, 24, 24, 12, k.primary);
                text_c(fb, hx + 12, h - 32, &hints[i][..1], BODY_SCALE, k.on_primary);
                text(fb, hx + 32, h - 33, &hints[i][2..], BODY_SCALE, k.on_surface_variant);
                hx += 120;
                i += 1;
            }
        }
    }
    Info { class, cols: if phone { 4 } else { 4 }, rows: 2, tiles: 4 }
}

/// The lock screen: Tide's paper, the accent lock glyph, the time and date, the notifications it is holding, and the gesture hint.
/// `device_mode` reads the glyph's accent edge back as proof this screen drew.
pub fn render_lockscreen(fb: &mut Fb, theme: Theme) -> Info {
    let k = match theme { Theme::Light => LIGHT, Theme::Dark => DARK };
    let (w, h) = (fb.w, fb.h);
    let class = size_class(w);
    fb.rect(0, 0, w, h, k.surface);
    let cx = w / 2;
    let gy = 56;
    outline(fb, cx - 10, gy, 20, 16, 8, k.on_surface_variant, k.surface);
    outline(fb, cx - 15, gy + 10, 30, 24, 6, k.primary, k.surface);
    fb.round(cx - 2, gy + 18, 4, 4, 2, k.primary);
    fb.rect(cx - 1, gy + 22, 2, 4, k.primary);
    text_c(fb, cx, gy + 48, b"12:30", 5, k.on_surface);
    text_c(fb, cx, gy + 48 + 7 * 5 + 12, b"THU 22 OCT", BODY_SCALE, k.on_surface_variant);
    let col_w = if w > 520 { 480 } else { w - 32 };
    let nx = (w - col_w) / 2;
    let mut ny = gy + 48 + 7 * 5 + 12 + 34;
    let mut ci = 0;
    while ci < 2 && ny + 44 <= h - 64 {
        fb.round(nx, ny, col_w, 38, 12, k.container_highest);
        text(fb, nx + 14, ny + 6, NOTES[ci].0, BODY_SCALE, k.on_surface);
        text(fb, nx + 14, ny + 22, NOTES[ci].1, BODY_SCALE, k.on_surface_variant);
        ny += 44;
        ci += 1;
    }
    text_c(fb, cx, h - 34, b"SWIPE UP TO UNLOCK", BODY_SCALE, k.on_surface_variant);
    Info { class, cols: 0, rows: 0, tiles: 0 }
}

/// Hash of the whole surface: h = h*31 + (pixel & 0xFFFFFF), 32-bit wrapping, row-major (same as the display service's band hash).
pub fn hash(fb: &Fb) -> u32 {
    let mut hh: u32 = 0; let mut y = 0;
    while y < fb.h { let mut x = 0; while x < fb.w { let p = unsafe { fb.base.add(y as usize * fb.pitch + x as usize).read_volatile() } & 0xFFFFFF; hh = hh.wrapping_mul(31).wrapping_add(p); x += 1; } y += 1; }
    hh
}
