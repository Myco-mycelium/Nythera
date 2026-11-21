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
//! The pane can hold one of the shell's two screens: its home screen, or
//! (with `nyrqis.lock=1`) its lock screen. The lock is a boot entry rather
//! than a key because nothing routes input into the shell yet -- the screen
//! renders and verifies, and unlocking is what a session will bring.
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

// Tide's palette, in the tokens these probes read: paper, glass, win-bar, accent, accent-fg, fg2.
pub const BG: u32 = 0x000b_171e;
pub const SURFACE: u32 = 0x0010_1f27;
pub const SURFACE_HI: u32 = 0x001a_323d;
pub const PRIMARY: u32 = 0x003c_c8c0;
pub const ON_PRIMARY: u32 = 0x0005_2321;
pub const OUTLINE: u32 = 0x008f_a8b5;

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

/// The content pane the shell gets: THE WHOLE SCREEN, in every mode.
///
/// Tide draws its own top bar, keel and posture layout; the kernel no longer
/// reserves a rail, an app bar, a status strip or a bottom strip for any mode.
/// Reserving any of them would mean the kernel drawing chrome beside Tide's,
/// which is the duplication this design removes -- a device mode now differs by
/// the POSTURE the shell draws (desktop windows, tablet lattice, phone grid,
/// console tile row), not by a frame around it.
/// Returned as (x, y, w, h).
pub fn content_pane(_mode: DeviceMode, _l: &Layout, w: u32, h: u32) -> (u32, u32, u32, u32) {
    (0, 0, w, h)
}

/// Draws the release's Material shell into the pane and verifies it read
/// back. The shell writes 0x00RRGGBB directly (that is its contract), so
/// the loader's scan-out order is applied to the pane afterwards -- one
/// conversion, in one place.
fn render_shell(fb: &mut Framebuffer, pane: (u32, u32, u32, u32), lock: bool, mode: DeviceMode) -> Option<Shell> {
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
    let posture = posture_of(mode);
    let info = if lock {
        shell::render_lockscreen(&mut surface, shell::Theme::Dark)
    } else {
        shell::render_posture(&mut surface, shell::Theme::Dark, posture)
    };
    let hash = shell::hash(&surface);
    fb.convert_scanout_region(x, y, w, h);
    // Probe a pixel only a real render paints: the clock widget's
    // `container` fill, 4px in from the widget's own left edge and past
    // its corner radius. The pane background is device_mode's BG, and the
    // dark theme's `surface` happens to be the same value -- so probing
    // the corner would have passed on an empty pane. `container` is a
    // different colour, so this pixel is evidence the shell ran.
    //
    // Tide's top bar starts with the brand tile, the only accent fill in the
    // bar and a pixel nothing else on the screen paints -- so it is evidence
    // the shell ran, not merely that the pane has a background colour. The lock
    // screen draws no top bar: its probe is the accent edge of the lock glyph's
    // body, which passes only if the body (not the hairline shackle) landed.
    let verified = if lock {
        fb.get(x + w / 2, y + 66) == Some(shell::DARK.primary)
    } else {
        fb.get(x + 11, y + 17) == Some(shell::DARK.primary)
    };
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

/// The device mode IS the posture: one shell, four arrangements, which is Tide's
/// own model (its mock switches `data-mode` between the same four).
fn posture_of(mode: DeviceMode) -> shell::Posture {
    match mode {
        DeviceMode::Desktop => shell::Posture::Desktop,
        DeviceMode::Tablet => shell::Posture::Tablet,
        DeviceMode::Phone => shell::Posture::Phone,
        DeviceMode::Console => shell::Posture::Console,
    }
}

/// Renders the shell as a SESSION IDENTITY wears it: the same Tide layout, with
/// the identity's name in the top bar's badge and its accent as the shell's one
/// accent. `crate::platform_mode` calls this instead of painting a second chrome
/// over the screen -- the persona is a name and a colour now, not another shell.
/// Returns whether the accent landed where it is read back (the brand tile).
pub fn render_identity(mode: DeviceMode, fb: &mut Framebuffer, name: &[u8], accent: u32) -> bool {
    let (w, h) = (fb.width, fb.height);
    fb.fill_rect(0, 0, w, h, BG);
    let (base, pitch) = fb.surface();
    let mut surface = shell::Fb {
        base,
        pitch,
        w: w as i32,
        h: h as i32,
    };
    shell::render_named(
        &mut surface,
        shell::Theme::Dark,
        posture_of(mode),
        Some((name, accent)),
    );
    fb.convert_scanout_region(0, 0, w, h);
    fb.get(11, 17) == Some(accent)
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
    render_with(mode, fb, false)
}

/// The same render with the shell's LOCK SCREEN in the content pane
/// instead of its home screen: the boot entry `nyrqis.lock=1` asks for
/// it, which is how a locked session is reachable before any input path
/// exists (nothing can unlock it yet -- that is the honest state). The
/// chrome, the layout and the read-back contract are identical, so a
/// lock-screen boot is still a device-mode boot.
pub fn render_locked(mode: DeviceMode, fb: &mut Framebuffer) -> Rendered {
    render_with(mode, fb, true)
}

fn render_with(mode: DeviceMode, fb: &mut Framebuffer, lock: bool) -> Rendered {
    let l = layout(mode, fb.width, fb.height);
    let w = fb.width;
    let h = fb.height;

    // Tide owns the screen. The kernel draws the paper and hands the shell the
    // whole display: its top bar, keel and posture layout ARE the chrome now.
    fb.fill_rect(0, 0, w, h, BG);
    let pane = content_pane(mode, &l, w, h);
    let shell_render = render_shell(fb, pane, lock, mode);

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
    // Tide's own landmarks, one per posture: the top bar's glass at the very
    // corner, the brand tile's accent, and the element that belongs to that
    // posture alone -- keel pill, gesture pill, or the console's hint bar. The
    // lock screen draws none of the chrome, so it is judged by its glyph alone.
    let posture_ok = match mode {
        // the keel's own padding row, clear of the tiles and their running dots
        DeviceMode::Desktop | DeviceMode::Tablet => fb.get(w / 2, h - 13) == Some(SURFACE_HI),
        DeviceMode::Phone => fb.get(w / 2, h - 12) == Some(OUTLINE),
        // the hint bar's own padding: the glyphs sit above it, so h-6 is glass
        DeviceMode::Console => fb.get(w / 2, h - 6) == Some(SURFACE),
    };
    if !lock {
        probes_ok &= fb.get(0, 0) == Some(SURFACE);
        probes_ok &= fb.get(11, 17) == Some(PRIMARY);
        probes_ok &= posture_ok;
    }

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
