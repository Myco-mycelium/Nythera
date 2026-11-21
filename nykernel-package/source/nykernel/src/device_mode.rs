//! Device modes -- one shell, four form factors.
//!
//! `desktop`, `tablet`, `phone`, `console` are not themes: each is a
//! layout *contract* (status strip height, navigation rail or bottom
//! bar, content width cap, column count, minimum touch target) that the
//! renderer below draws and the shell port consumes. Selection is a real
//! decision made per machine at boot:
//!
//!   explicit   `nyrqis.mode=phone` on the loader's kernel command line
//!              wins, and an unknown token is reported rather than
//!              silently ignored;
//!   automatic  by display class, because that is the only form-factor
//!              signal this kernel can measure without a shell running:
//!              no framebuffer -> console, < 800 px wide -> phone,
//!              < 1400 px -> tablet, otherwise desktop.
//!
//! What is real here: the geometry, the chrome, the shell, and the
//! read-back check. Each mode reserves a *content pane* by its own
//! contract and release 1-71's shell (`shell::render`, compiled from
//! `porting/release-1-71/shell/src/ui.rs`) -- Android's home screen plus
//! Windows' taskbar and window controls, in the HIG's visual language --
//! is drawn into
//! it -- the same source the release's user-space `nyshell` and the host
//! checker run. Because the pane's width differs per mode, the shell's
//! own window-size-class logic picks a different real layout in each
//! one: that is the adaptation, not a per-mode copy of the shell.
//!
//! What is still not here: the shell is a renderer, not a session. There
//! is no input routing into it and no user-space process owning it
//! (`userprog/desk` needs the `0x84` IPC/capability/shm surface, which is
//! staged, not ported). Saying "the phone mode renders the shell" means
//! the shell's pixels are on the display and verified;
//! it does not mean a touch on it does anything yet.

use core::sync::atomic::{AtomicU8, Ordering};

use crate::devices::framebuffer::Framebuffer;
use crate::devices::Hardware;
use crate::shell;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DeviceMode {
    Desktop,
    Tablet,
    Phone,
    Console,
}

impl DeviceMode {
    pub fn as_str(self) -> &'static str {
        match self {
            DeviceMode::Desktop => "desktop",
            DeviceMode::Tablet => "tablet",
            DeviceMode::Phone => "phone",
            DeviceMode::Console => "console",
        }
    }

    /// ASCII token -> mode. Deliberately exact: a typo in a boot entry
    /// must not silently select the default and look like it worked.
    pub fn parse(token: &[u8]) -> Option<Self> {
        match token {
            b"desktop" => Some(DeviceMode::Desktop),
            b"tablet" => Some(DeviceMode::Tablet),
            b"phone" => Some(DeviceMode::Phone),
            b"console" => Some(DeviceMode::Console),
            _ => None,
        }
    }

    /// Stable numeric identity for the `0x84` gate: the order `select`
    /// prints, and the same value `PLATFORM_INFO` reports. Not a wire
    /// format beyond this kernel.
    pub const fn id(self) -> u64 {
        match self {
            DeviceMode::Desktop => 0,
            DeviceMode::Tablet => 1,
            DeviceMode::Phone => 2,
            DeviceMode::Console => 3,
        }
    }

    pub fn from_id(id: u64) -> Option<Self> {
        match id {
            0 => Some(DeviceMode::Desktop),
            1 => Some(DeviceMode::Tablet),
            2 => Some(DeviceMode::Phone),
            3 => Some(DeviceMode::Console),
            _ => None,
        }
    }

    const fn code(self) -> u8 {
        self.id() as u8
    }

    /// The mode a display class implies, with no explicit request.
    fn for_display(width: Option<u32>) -> Self {
        match width {
            None => DeviceMode::Console,
            Some(w) if w < 800 => DeviceMode::Phone,
            Some(w) if w < 1400 => DeviceMode::Tablet,
            Some(_) => DeviceMode::Desktop,
        }
    }
}

/// The mode selected for THIS machine, published so kernel code that is
/// not handed the boot session's layout (the `0x84` identity rows) can
/// re-render the same form factor a caller is looking at. Defaults to
/// desktop until `select` runs, which every boot does before any draw.
static MODE: AtomicU8 = AtomicU8::new(DeviceMode::Desktop.code());

/// Publishes a runtime form-factor request. The caller (the `0x84`
/// `PLATFORM_MODE_SET` row) has already re-rendered and verified the new
/// layout; this makes the override the value every later consumer reads,
/// so the identity switch, `PLATFORM_INFO` and a second request cannot
/// disagree about which layout is on screen.
pub fn set_current(mode: DeviceMode) {
    MODE.store(mode.code(), Ordering::Relaxed);
}

pub fn current() -> DeviceMode {
    match MODE.load(Ordering::Relaxed) {
        1 => DeviceMode::Tablet,
        2 => DeviceMode::Phone,
        3 => DeviceMode::Console,
        _ => DeviceMode::Desktop,
    }
}

/// A layout contract in pixels. Sizes are the design tokens the shell
/// port reads; keeping them here (not inside the renderer) is what lets
/// the shell and this renderer agree without a second source of truth.
#[derive(Debug, Clone, Copy)]
pub struct Layout {
    pub status: u32,
    pub rail: u32,
    pub appbar: u32,
    pub bottom_nav: u32,
    pub margin: u32,
    pub columns: u32,
    pub content_w: u32,
    pub touch_min: u32,
}

const MODE_TOKEN: &[u8] = b"nyrqis.mode=";

/// Parses the `nyrqis.mode=` token out of a Multiboot2 command line.
/// Returns `(mode, valid)`: a present-but-unknown token is reported so
/// the boot log names the mistake instead of the fallback hiding it.
pub fn parse_cmdline(cmdline: &[u8]) -> Option<(Option<DeviceMode>, &'static str)> {
    let mut i = 0;
    while i + MODE_TOKEN.len() <= cmdline.len() {
        if &cmdline[i..i + MODE_TOKEN.len()] == MODE_TOKEN {
            let rest = &cmdline[i + MODE_TOKEN.len()..];
            let end = rest
                .iter()
                .position(|&b| b == b' ' || b == 0)
                .unwrap_or(rest.len());
            let token = &rest[..end];
            return Some(match DeviceMode::parse(token) {
                Some(m) => (Some(m), "kernel cmdline"),
                None => (None, "invalid nyrqis.mode token"),
            });
        }
        i += 1;
    }
    None
}

/// The per-machine decision, and the reason for it.
pub fn select(hardware: &Hardware, cmdline: &[u8]) -> (DeviceMode, &'static str, Option<&'static str>) {
    let decision = match parse_cmdline(cmdline) {
        Some((Some(mode), source)) => {
            // A console request on a machine with a framebuffer is
            // honoured (the loader may be serving a serial-only
            // environment) but still says where it came from.
            (mode, source, None)
        }
        Some((None, _)) => (
            DeviceMode::for_display(hardware.fb.map(|f| f.width)),
            "display class",
            Some("nyrqis.mode token is not one of desktop|tablet|phone|console"),
        ),
        None => (
            DeviceMode::for_display(hardware.fb.map(|f| f.width)),
            "display class",
            None,
        ),
    };
    MODE.store(decision.0.code(), Ordering::Relaxed);
    decision
}

/// The geometry for a mode on a given display. `content_w` is the
/// *maximum* content width (a phone layout centred on a wide screen
/// keeps its phone width, which is the Android behaviour the design
/// language specifies), never a stretch target.
pub fn layout(mode: DeviceMode, width: u32, height: u32) -> Layout {
    let _ = height;
    match mode {
        DeviceMode::Desktop => Layout {
            status: 28,
            rail: 80,
            appbar: 56,
            bottom_nav: 0,
            margin: 24,
            columns: 3,
            content_w: width,
            touch_min: 40,
        },
        DeviceMode::Tablet => Layout {
            status: 28,
            rail: 0,
            appbar: 64,
            bottom_nav: 0,
            margin: 24,
            columns: 2,
            content_w: width,
            touch_min: 48,
        },
        DeviceMode::Phone => Layout {
            status: 24,
            rail: 0,
            appbar: 56,
            bottom_nav: 56,
            margin: 16,
            columns: 1,
            content_w: width.min(480),
            touch_min: 48,
        },
        DeviceMode::Console => Layout {
            status: 16,
            rail: 0,
            appbar: 0,
            bottom_nav: 0,
            margin: 12,
            columns: 1,
            content_w: width,
            touch_min: 0,
        },
    }
}

pub const BG: u32 = 0x0014_1218;
pub const SURFACE: u32 = 0x001c_1b1f;
pub const SURFACE_HI: u32 = 0x0024_2329;
pub const PRIMARY: u32 = 0x00d0_bcff;
pub const ON_PRIMARY: u32 = 0x0038_1e72;
pub const OUTLINE: u32 = 0x0049_454f;
pub const CONSOLE_BG: u32 = 0x000b_0b0e;
pub const CONSOLE_FG: u32 = 0x00b6_f2c8;

/// The shell render, as facts: where it went, which of its own window
/// size classes it chose, and its surface hash (computed over the pane
/// before the loader's channel order is applied, so the number is
/// comparable across machines with a different scan-out order).
pub struct Shell {
    pub pane: (u32, u32, u32, u32),
    pub class: shell::SizeClass,
    pub cols: i32,
    pub rows: i32,
    pub tiles: i32,
    pub hash: u32,
    pub verified: bool,
}

/// The content pane each mode leaves for the shell, by that mode's own
/// contract: desktop reserves the rail, status strip and app bar; tablet the
/// status strip and app bar; phone the status strip, app bar and the centred
/// content cap. NO mode reserves a bottom strip: the shell draws its own
/// taskbar inside the pane, and that bar is the one bottom bar on the screen.
/// The pane therefore runs to the display's last row -- a mode that reserved
/// the bottom too would draw a second bar under the shell's, which is exactly
/// the duplication this contract removes.
/// Returned as (x, y, w, h).
pub fn content_pane(mode: DeviceMode, l: &Layout, w: u32, h: u32) -> (u32, u32, u32, u32) {
    match mode {
        DeviceMode::Desktop => {
            let y = l.status + l.appbar;
            (l.rail, y, w.saturating_sub(l.rail + l.margin), h.saturating_sub(y))
        }
        DeviceMode::Tablet => {
            let y = l.status + l.appbar;
            (0, y, w, h.saturating_sub(y))
        }
        DeviceMode::Phone => {
            let cw = l.content_w.min(w);
            let y = l.status + l.appbar;
            ((w - cw) / 2, y, cw, h.saturating_sub(y))
        }
        DeviceMode::Console => (0, 0, 0, 0),
    }
}

/// Draws the release's Material shell into the pane and verifies it read
/// back. The shell writes 0x00RRGGBB directly (that is its contract), so
/// the loader's scan-out order is applied to the pane afterwards -- one
/// conversion, in one place.
fn render_shell(fb: &mut Framebuffer, pane: (u32, u32, u32, u32)) -> Option<Shell> {
    let (x, y, w, h) = pane;
    if w < 64 || h < 64 {
        return None;
    }
    let (base, pitch) = fb.surface();
    let mut surface = shell::Fb {
        base: unsafe { base.add(y as usize * pitch + x as usize) },
        pitch,
        w: w as i32,
        h: h as i32,
    };
    let info = shell::render(&mut surface, shell::Theme::Dark);
    let hash = shell::hash(&surface);
    fb.convert_scanout_region(x, y, w, h);
    // Probe a pixel only a real render paints: the clock widget's
    // `container` fill, 4px in from the widget's own left edge and past
    // its corner radius. The pane background is device_mode's BG, and the
    // dark theme's `surface` happens to be the same value -- so probing
    // the corner would have passed on an empty pane. `container` is a
    // different colour, so this pixel is evidence the shell ran.
    let m = if info.class == shell::SizeClass::Compact { 16 } else { 24 };
    let verified = fb.get(x + m + 4, y + 70) == Some(shell::DARK.container);
    Some(Shell {
        pane,
        class: info.class,
        cols: info.cols,
        rows: info.rows,
        tiles: info.tiles,
        hash,
        verified,
    })
}

/// What the renderer wrote, for the boot transcript and the test.
pub struct Rendered {
    pub layout: Layout,
    pub shell: Option<Shell>,
    /// FNV-1a over every 13th pixel. Two modes that draw different
    /// geometry produce different values; the boot test asserts that
    /// and checks the same values against a QEMU screendump.
    pub checksum: u32,
    pub probes_ok: bool,
}

fn fnv1a(mut h: u32, v: u32) -> u32 {
    for b in v.to_le_bytes() {
        h ^= b as u32;
        h = h.wrapping_mul(0x0100_0193);
    }
    h
}

/// Draws the mode's chrome and returns the layout, a content checksum
/// and whether the pixels read back. Drawing is idempotent: the same
/// mode on the same display always produces the same checksum.
pub fn render(mode: DeviceMode, fb: &mut Framebuffer) -> Rendered {
    let l = layout(mode, fb.width, fb.height);
    let w = fb.width;
    let h = fb.height;
    // Phone content width and its left edge: the layout contract caps the
    // content width on a wide screen, and centring is part of the
    // contract, not a renderer detail -- the probes below read it back.
    let cw = l.content_w.min(w);
    let x0 = (w - cw) / 2;

    let pane = content_pane(mode, &l, w, h);
    match mode {
        DeviceMode::Console => render_console(fb, &l),
        DeviceMode::Desktop => {
            fb.fill_rect(0, 0, w, h, BG);
            // status strip + the accent block every mode carries
            fb.fill_rect(0, 0, w, l.status, SURFACE);
            fb.fill_rect(l.margin, l.status / 2 - 4, 8, 8, PRIMARY);
            // navigation rail with four destination tiles
            fb.fill_rect(0, l.status, l.rail, h - l.status, SURFACE);
            let tile = 48;
            for i in 0..4u32 {
                let y = l.status + 24 + i * (tile + 16);
                fb.fill_rect(16, y, tile, tile, if i == 0 { PRIMARY } else { SURFACE_HI });
                if i == 0 {
                    fb.fill_rect(32, y + 20, tile - 32, 8, ON_PRIMARY);
                }
            }
            // app bar (the three placeholder cards that used to sit under
            // it are gone: the shell fills that area now)
            let cx = l.rail + l.margin;
            // `bar_w`, not `cw`: `cw` above is the LAYOUT's content width
            // (the phone contract's cap), this is the app bar's own span.
            let bar_w = w - cx - l.margin;
            fb.fill_rect(cx, l.status, bar_w, l.appbar, SURFACE);
            fb.fill_rect(cx + l.margin, l.status + 20, 160, 16, SURFACE_HI);
            // No dock here: the shell's taskbar is this mode's bottom bar, and
            // it lives at the pane's last row. A dock under it was a second
            // launcher for the same machine.
        }
        DeviceMode::Tablet => {
            fb.fill_rect(0, 0, w, h, BG);
            fb.fill_rect(0, 0, w, l.status, SURFACE);
            fb.fill_rect(l.margin, l.status / 2 - 4, 8, 8, PRIMARY);
            fb.fill_rect(0, l.status, w, l.appbar, SURFACE);
            fb.fill_rect(l.margin, l.status + 24, 200, 16, SURFACE_HI);
            // No gesture pill: the shell's taskbar owns the bottom edge now,
            // and a gesture inset under it would be a second navigation model.
        }
        DeviceMode::Phone => {
            fb.fill_rect(0, 0, w, h, BG);
            fb.fill_rect(0, 0, w, l.status, SURFACE);
            fb.fill_rect(l.margin, l.status / 2 - 4, 8, 8, PRIMARY);
            fb.fill_rect(x0, l.status, cw, l.appbar, SURFACE);
            fb.fill_rect(x0 + l.margin, l.status + 20, 120, 16, SURFACE_HI);
            // No bottom navigation: the shell's taskbar is the phone's bottom
            // bar as well, which is the point of the union -- one bar, one set
            // of functions, on every form factor.
        }
    }

    // The shell goes in AFTER the chrome that frames it and BEFORE the
    // controls that float over content -- the FAB is drawn below, so the
    // shell cannot swallow it.
    let shell_render = render_shell(fb, pane);
    if mode == DeviceMode::Phone {
        let fab_r = 28;
        // Clear of the shell's taskbar: the FAB floats over content, and the
        // content pane now runs to the display's last row.
        let card_bottom = h - shell::bar_height(cw as i32) as u32 - l.margin;
        fb.fill_circle(
            x0 + cw - l.margin - fab_r,
            card_bottom - fab_r - 16,
            fab_r,
            PRIMARY,
        );
    }

    // Read the pixels back through the same mapping and verify each
    // mode's OWN invariants -- the strip, the accent block every mode
    // shares, and one landmark that only that layout draws. A single
    // "centre is not background" probe was the first version of this and
    // it failed on the tablet layout's centred gap: the invariant has to
    // be a fact the renderer promises, not a guess about the middle.
    let mut probes_ok = true;
    // A shell that rendered but did not survive the read-back is a
    // failure of this renderer, so it fails the same assertion as the
    // chrome probes rather than printing a mismatch beside a pass.
    if let Some(s) = shell_render.as_ref() {
        probes_ok &= s.verified;
    }
    probes_ok &= fb.get(0, 0) == Some(SURFACE);
    probes_ok &= fb.get(l.margin + 2, l.status / 2) == Some(PRIMARY);
    probes_ok &= match mode {
        DeviceMode::Desktop => {
            // rail surface at its right edge, and the shell's one bottom bar
            // reaching the display's last rows (its Start mark is the accent).
            // The bar probe is PANE-relative: this mode's content starts at the
            // rail, so the screen's own margin is not where the bar begins.
            fb.get(l.rail - 4, l.status + 4) == Some(SURFACE)
                && fb.get(pane.0 + l.margin + 4, h - 22) == Some(PRIMARY)
        }
        DeviceMode::Tablet => {
            // app bar spans the screen; the shell's bottom bar reaches the edge
            fb.get(w - 1, l.status + 4) == Some(SURFACE)
                && fb.get(pane.0 + l.margin + 4, h - 22) == Some(PRIMARY)
        }
        DeviceMode::Phone => {
            // the FAB centre is accent, and it floats above the bottom bar the
            // shell draws (there is no separate bottom navigation to sit on)
            let fab_r = 28;
            fb.get(x0 + cw - l.margin - fab_r,
                  h - shell::bar_height(cw as i32) as u32 - l.margin - fab_r - 16) == Some(PRIMARY)
                && fb.get(pane.0 + l.margin + 4, h - 22) == Some(PRIMARY)
        }
        DeviceMode::Console => {
            fb.get(l.margin + 12, l.status + l.margin + 12) == Some(CONSOLE_FG)
        }
    };

    let mut checksum = 0x811c_9dc5u32;
    let mut idx = 0usize;
    let total = (w as usize) * (h as usize);
    while idx < total {
        let x = (idx % w as usize) as u32;
        let y = (idx / w as usize) as u32;
        if let Some(px) = fb.get(x, y) {
            checksum = fnv1a(checksum, px);
        }
        idx += 13;
    }

    Rendered {
        layout: l,
        shell: shell_render,
        checksum,
        probes_ok,
    }
}

/// Draws a text run through the shell's own font and applies the
/// loader's scan-out order to exactly the rows it wrote. Shared with
/// `crate::platform_mode`, so the console and every persona's wordmark
/// speak with one font implementation instead of two.
pub fn draw_text(fb: &mut Framebuffer, x: u32, y: u32, s: &[u8], scale: i32, color: u32) {
    if scale <= 0 || x >= fb.width || y >= fb.height {
        return;
    }
    let (base, pitch) = fb.surface();
    let mut surface = shell::Fb {
        base,
        pitch,
        w: fb.width as i32,
        h: fb.height as i32,
    };
    shell::text(&mut surface, x as i32, y as i32, s, scale, color);
    let tw = shell::text_w(s, scale).max(0) as u32 + scale as u32;
    fb.convert_scanout_region(x, y, tw.min(fb.width - x), (7 * scale) as u32);
}

/// Console mode: no window chrome, because there is no window manager
/// in it -- a full-screen terminal surface with a frame, a prompt caret
/// and a scroll strip. Drawn with the same framebuffer, so the four
/// modes are one renderer, not four code paths with different rules.
fn render_console(fb: &mut Framebuffer, l: &Layout) {
    let w = fb.width;
    let h = fb.height;
    fb.fill_rect(0, 0, w, h, CONSOLE_BG);
    fb.fill_rect(0, 0, w, l.status, SURFACE);
    // The accent block every mode carries, in the same accent colour: a
    // layout difference must be geometry, never a different palette, or
    // the four modes would stop being one shell.
    fb.fill_rect(l.margin, l.status / 2 - 4, 8, 8, PRIMARY);
    fb.stroke_rect(l.margin, l.status + l.margin, w - l.margin * 2, h - l.status - l.margin * 2, OUTLINE);
    // prompt caret
    fb.fill_rect(l.margin + 12, l.status + l.margin + 12, 3, 18, CONSOLE_FG);
    // Real glyphs, not rule-block stand-ins: the release's 5x7 font (the
    // same table the shell draws its labels with) puts the session's
    // facts on the console surface. The font carries A-Z, 0-9, space and
    // colon, so the console speaks in that alphabet rather than growing
    // a second, half-complete font of its own.
    let line = l.status + l.margin + 12;
    draw_text(fb, l.margin + 24, line, b"NYRQIS>", 1, CONSOLE_FG);
    draw_text(fb, l.margin + 24, line + 20, b"MODE CONSOLE", 1, SURFACE_HI);
    draw_text(fb, l.margin + 24, line + 38, b"BOOT OK", 1, CONSOLE_FG);
    draw_text(fb, l.margin + 24, line + 56, b"12:30", 1, SURFACE_HI);
    // scroll strip
    fb.fill_rect(w - l.margin - 10, l.status + l.margin + 8, 4, h / 3, OUTLINE);
}
