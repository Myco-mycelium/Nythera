//! Platform personas -- one kernel, five session identities.
//!
//! `crate::device_mode` answers "what shape is this panel?" (desktop,
//! tablet, phone, console). This module answers the other half of the
//! same question: *whose* desktop does the session present on it --
//! Nyrqis's own, or the Android, Windows, macOS or console posture a
//! user of that device family already knows. The two decisions are
//! independent on purpose: a Windows posture on a tablet is a real
//! combination, and so is a phone panel with the native identity.
//!
//! Selection is a real per-machine decision, printed with its reason:
//!
//!   explicit   `nyrqis.persona=nyrqis|android|windows|macos|console`
//!              on the loader's command line. An unknown token is
//!              REPORTED (and the detected identity used), never
//!              silently swallowed.
//!
//!   automatic  the firmware's own signatures first -- the ACPI FADT's
//!              OEM ID (an Apple or Microsoft machine names itself
//!              there) and the PCI vendor IDs on the bus -- then the
//!              display class (a phone-sized panel is an Android
//!              posture), then the native Nyrqis identity. Each answer
//!              carries the signal it came from.
//!
//! Switching is a runtime operation, not a boot-time only choice: the
//! session can move between identities with the number keys 1..5 during
//! the boot session's hotkey window, and every switch re-renders through
//! `crate::device_mode::render` first, so a previous posture's pixels can
//! never leak under the new one. Each apply reads its own landmarks back
//! and publishes a surface hash, so "the switch happened" is measured,
//! not inferred from a menu entry.
//!
//! Scope, stated plainly: this is a session SKIN and identity contract,
//! not four compatibility layers. It picks the geometry each family
//! expects (top menu bar, bottom taskbar, gesture pill, terminal frame)
//! and the palette that goes with it; it does not make Windows PE
//! imports or Android DEX bytecode run any differently -- those live in
//! `experimental::pe` / `experimental::dex` and are unaffected by which
//! identity is on screen.

use core::sync::atomic::{AtomicU8, Ordering};

use crate::acpi::AcpiInfo;
use crate::device_mode::{self, DeviceMode};
use crate::devices::framebuffer::Framebuffer;
use crate::devices::Hardware;
use crate::serial_println;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Platform {
    /// The kernel's own identity: the mycelium signature.
    Nyrqis,
    Android,
    Windows,
    MacOs,
    Console,
}

impl Platform {
    /// The runtime switch order, and the order the number keys 1..5 map
    /// to. Fixed and printed, so a hotkey cannot mean two things.
    pub const ALL: [Platform; 5] = [
        Platform::Nyrqis,
        Platform::Android,
        Platform::Windows,
        Platform::MacOs,
        Platform::Console,
    ];

    pub fn as_str(self) -> &'static str {
        match self {
            Platform::Nyrqis => "nyrqis",
            Platform::Android => "android",
            Platform::Windows => "windows",
            Platform::MacOs => "macos",
            Platform::Console => "console",
        }
    }

    /// ASCII token -> identity. Exact on purpose: a typo in a boot entry
    /// must be reported, not quietly become the default.
    pub fn parse(token: &[u8]) -> Option<Self> {
        match token {
            b"nyrqis" => Some(Platform::Nyrqis),
            b"android" => Some(Platform::Android),
            b"windows" => Some(Platform::Windows),
            b"macos" => Some(Platform::MacOs),
            b"console" => Some(Platform::Console),
            _ => None,
        }
    }

    /// Stable numeric identity for the `0x84` gate: the index into
    /// `Platform::ALL`, which is also the order the boot transcript
    /// prints. An unknown number is `None`, never a default.
    pub const fn id(self) -> u64 {
        match self {
            Platform::Nyrqis => 0,
            Platform::Android => 1,
            Platform::Windows => 2,
            Platform::MacOs => 3,
            Platform::Console => 4,
        }
    }

    pub fn from_id(id: u64) -> Option<Self> {
        match id {
            0 => Some(Platform::Nyrqis),
            1 => Some(Platform::Android),
            2 => Some(Platform::Windows),
            3 => Some(Platform::MacOs),
            4 => Some(Platform::Console),
            _ => None,
        }
    }

    const fn code(self) -> u8 {
        self.id() as u8
    }

    fn from_code(code: u8) -> Self {
        match code {
            1 => Platform::Android,
            2 => Platform::Windows,
            3 => Platform::MacOs,
            4 => Platform::Console,
            _ => Platform::Nyrqis,
        }
    }

    /// The identity's colour roles. Kept here (not inside a renderer) so
    /// the skin and the boot transcript agree without a second source of
    /// truth, the same rule `device_mode`'s layout tokens follow.
    pub fn palette(self) -> Palette {
        match self {
            // Bio-luminescent mint on deep ink -- the kernel's own look.
            Platform::Nyrqis => Palette {
                bg: 0x000b_0f14,
                surface: 0x0012_1c24,
                surface_hi: 0x001b_2a36,
                accent: 0x002e_eea6,
                on_accent: 0x0004_2419,
                text: 0x00d7_f5ff,
                muted: 0x0076_8f9f,
            },
            // Material You baseline blue, the tone Android 17 ships.
            Platform::Android => Palette {
                bg: 0x0011_1216,
                surface: 0x001c_1f24,
                surface_hi: 0x0027_2b31,
                accent: 0x00a8_c7ff,
                on_accent: 0x0000_294a,
                text: 0x00ff_ffff,
                muted: 0x0090_9aa6,
            },
            // Windows 11 dark: neutral greys, one saturated accent.
            Platform::Windows => Palette {
                bg: 0x001f_1f1f,
                surface: 0x002c_2c2c,
                surface_hi: 0x0038_3838,
                accent: 0x004c_c2ff,
                on_accent: 0x0000_1a2b,
                text: 0x00ff_ffff,
                muted: 0x00b0_b0b0,
            },
            // macOS dark: cool greys, system blue, the traffic lights.
            Platform::MacOs => Palette {
                bg: 0x001e_1e21,
                surface: 0x002c_2c30,
                surface_hi: 0x003a_3a40,
                accent: 0x000a_84ff,
                on_accent: 0x00ff_ffff,
                text: 0x00f2_f2f7,
                muted: 0x00a0_a0aa,
            },
            // Console: a terminal, not a desktop.
            Platform::Console => Palette {
                bg: 0x0009_0b0a,
                surface: 0x0010_1613,
                surface_hi: 0x001a_2420,
                accent: 0x003d_dc84,
                on_accent: 0x0000_0b06,
                text: 0x00c9_ffd6,
                muted: 0x0068_8f74,
            },
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub struct Palette {
    pub bg: u32,
    pub surface: u32,
    pub surface_hi: u32,
    pub accent: u32,
    pub on_accent: u32,
    pub text: u32,
    pub muted: u32,
}

/// The identity the session is presenting right now, published for
/// consumers outside the framebuffer's borrow (the `0x84` surface, a
/// future shell port) without either of them needing the display.
static CURRENT: AtomicU8 = AtomicU8::new(Platform::Nyrqis.code());

pub fn current() -> Platform {
    Platform::from_code(CURRENT.load(Ordering::Relaxed))
}

const TOKEN: &[u8] = b"nyrqis.persona=";
const TOKEN_SELFTEST: &[u8] = b"nyrqis.persona_selftest=";

/// Reads `key`'s value out of a Multiboot2 command line. Bounded and
/// ASCII-token based, exactly like `device_mode`'s parser: a cmdline is
/// untrusted input, not UTF-8 text.
fn cmd_value<'a>(cmdline: &'a [u8], key: &[u8]) -> Option<&'a [u8]> {
    let mut i = 0;
    while i + key.len() <= cmdline.len() {
        if &cmdline[i..i + key.len()] == key {
            let rest = &cmdline[i + key.len()..];
            let end = rest
                .iter()
                .position(|&b| b == b' ' || b == 0)
                .unwrap_or(rest.len());
            return Some(&rest[..end]);
        }
        i += 1;
    }
    None
}

/// Whether this boot asked for the identity-cycle proof. The cycle draws
/// every persona before restoring the selected one, so it runs where a
/// full render proof belongs: the `qemu_test` boot gate, or an explicit
/// `nyrqis.persona_selftest=1` request. A normal boot applies the chosen
/// skin once and stays there.
pub fn selftest_requested(cmdline: &[u8]) -> bool {
    matches!(cmd_value(cmdline, TOKEN_SELFTEST), Some(b"1") | Some(b"on"))
}

/// The per-machine decision and the signal behind it.
fn detect(
    hardware: &Hardware,
    acpi: Option<&AcpiInfo>,
    mode: DeviceMode,
) -> (Platform, &'static str) {
    if hardware.fb.is_none() {
        return (Platform::Console, "no framebuffer: terminal session");
    }
    if let Some(info) = acpi {
        if info.oem_id().starts_with(b"APPLE") {
            return (Platform::MacOs, "firmware: Apple ACPI OEM ID");
        }
        if info.oem_id().starts_with(b"MSFT") {
            return (Platform::Windows, "firmware: Microsoft ACPI OEM ID");
        }
    }
    if hardware.pci.has_vendor(0x106b) {
        return (Platform::MacOs, "firmware: Apple PCI vendor 0x106b");
    }
    if hardware.pci.has_vendor(0x1414) {
        return (Platform::Windows, "firmware: Microsoft PCI vendor 0x1414");
    }
    // The PANEL decides, not the requested layout: a phone-sized display
    // is where an Android posture belongs, while an explicit
    // `nyrqis.mode=phone` on a desktop panel is a layout choice and must
    // not silently change the identity too.
    let _ = mode;
    if let Some(info) = hardware.fb {
        if info.width < 800 {
            return (Platform::Android, "display class: phone-sized panel");
        }
    }
    (Platform::Nyrqis, "default: no platform signal, native identity")
}

/// The boot decision: explicit cmdline token first, then the firmware /
/// display-class detection. `(identity, source, problem)` -- a present
/// but unknown token is reported rather than hidden by the fallback.
pub fn select(
    hardware: &Hardware,
    acpi: Option<&AcpiInfo>,
    cmdline: &[u8],
    mode: DeviceMode,
) -> (Platform, &'static str, Option<&'static str>) {
    let (detected, detected_source) = detect(hardware, acpi, mode);
    match cmd_value(cmdline, TOKEN) {
        Some(token) => match Platform::parse(token) {
            Some(platform) => (platform, "kernel cmdline", None),
            None => (
                detected,
                detected_source,
                Some("nyrqis.persona token is not one of nyrqis|android|windows|macos|console"),
            ),
        },
        None => (detected, detected_source, None),
    }
}

/// What one apply wrote, for the transcript and the boot proof.
pub struct Skin {
    pub platform: Platform,
    /// The status-strip height the identity drew its bar into.
    pub bar: u32,
    /// FNV-1a over every 13th pixel: two identities that draw different
    /// chrome produce different values, and the cycle proof asserts it.
    pub hash: u32,
    pub verified: bool,
}

fn surface_hash(fb: &Framebuffer) -> u32 {
    let mut h = 0x811c_9dc5u32;
    let total = (fb.width as usize) * (fb.height as usize);
    let mut idx = 0usize;
    while idx < total {
        let x = (idx % fb.width as usize) as u32;
        let y = (idx / fb.width as usize) as u32;
        if let Some(px) = fb.get(x, y) {
            for b in px.to_le_bytes() {
                h ^= b as u32;
                h = h.wrapping_mul(0x0100_0193);
            }
        }
        idx += 13;
    }
    h
}

fn text_right(fb: &mut Framebuffer, right: u32, y: u32, s: &[u8], scale: i32, color: u32) {
    let tw = crate::shell::text_w(s, scale).max(0) as u32;
    let x = right.saturating_sub(tw);
    device_mode::draw_text(fb, x, y, s, scale, color);
}

/// The native identity's signature: a spore cluster (the mycelium mark)
/// with its satellite spores, the wordmark to its left, and a two-pixel
/// accent rule under the status strip. Drawn into the right-hand end of
/// the strip so the layout landmarks every device mode provides -- the
/// accent block at the strip's left, the dock, the FAB, the shell's own
/// pane -- are left exactly as the device renderer drew them.
fn paint_nyrqis(fb: &mut Framebuffer, l: &device_mode::Layout) {
    let p = Platform::Nyrqis.palette();
    let cx = fb.width.saturating_sub(26);
    let cy = (l.status / 2).max(5);
    fb.fill_circle(cx, cy, 5, p.accent);
    fb.fill_rect(cx.saturating_sub(cx.min(2)), cy.saturating_sub(1), 4, 2, p.accent);
    fb.fill_circle(cx.saturating_sub(14), cy.saturating_sub(1), 2, p.muted);
    fb.fill_circle(cx.saturating_add(12), cy.saturating_add(1), 2, p.muted);
    text_right(
        fb,
        cx.saturating_sub(10),
        cy.saturating_sub(3),
        b"NYRQIS",
        1,
        p.text,
    );
    fb.fill_rect(
        l.margin,
        l.status.saturating_sub(2),
        fb.width.saturating_sub(l.margin * 2),
        2,
        p.muted,
    );
}

/// Android: a status strip with an accent chip, and the gesture pill at
/// the bottom of the panel. Material's shape language, nothing else.
fn paint_android(fb: &mut Framebuffer, l: &device_mode::Layout) {
    let p = Platform::Android.palette();
    let w = fb.width;
    let h = fb.height;
    fb.fill_rect(0, 0, w, l.status, p.surface);
    let chip_x = w.saturating_sub(16);
    let chip_y = (l.status / 2).saturating_sub(1);
    fb.fill_rect(chip_x.saturating_sub(4), chip_y, 8, 3, p.accent);
    text_right(
        fb,
        chip_x.saturating_sub(12),
        (l.status / 2).saturating_sub(3),
        b"ANDROID",
        1,
        p.text,
    );
    if h > 96 {
        let (px, py) = (w / 2, h.saturating_sub(12));
        fb.fill_rect(px.saturating_sub(40), py, 80, 5, p.muted);
        fb.fill_circle(px.saturating_sub(40), py + 2, 2, p.muted);
        fb.fill_circle(px.saturating_add(40), py + 2, 2, p.muted);
    }
}

/// Windows: a bottom taskbar with the four-square start mark, and a
/// window title bar over the content pane with minimise / maximise /
/// close controls. Windows' identity is the taskbar, so the panel's
/// top strip keeps the device renderer's own chrome.
fn paint_windows(fb: &mut Framebuffer, mode: DeviceMode, l: &device_mode::Layout) {
    let p = Platform::Windows.palette();
    let w = fb.width;
    let h = fb.height;
    let bar = (h / 8).clamp(28, 48);
    let ty = h.saturating_sub(bar);
    fb.fill_rect(0, ty, w, bar, p.surface);
    let lx = w / 2 - 9;
    let ly = ty + bar / 2 - 9;
    for &(dx, dy) in &[(0u32, 0u32), (10, 0), (0, 10), (10, 10)] {
        fb.fill_rect(lx + dx, ly + dy, 8, 8, p.accent);
    }
    device_mode::draw_text(fb, 16, ty + bar / 2 - 3, b"NYRQIS", 1, p.text);
    let pane = device_mode::content_pane(mode, l, w, h);
    if pane.2 > 200 && pane.3 > 96 {
        let (px, py, pw) = (pane.0, pane.1, pane.2);
        fb.fill_rect(px, py, pw, 26, p.surface_hi);
        device_mode::draw_text(fb, px + 10, py + 9, b"NYRQIS", 1, p.muted);
        fb.fill_rect(px + pw - 58, py + 12, 8, 2, p.text);
        fb.stroke_rect(px + pw - 40, py + 9, 8, 8, p.text);
        fb.stroke_rect(px + pw - 18, py + 9, 8, 8, p.accent);
    }
}

/// macOS: a top menu bar (mark, menus, clock), a bottom dock, and the
/// traffic lights on the content window.
fn paint_macos(fb: &mut Framebuffer, mode: DeviceMode, l: &device_mode::Layout) {
    let p = Platform::MacOs.palette();
    let w = fb.width;
    let h = fb.height;
    fb.fill_rect(0, 0, w, l.status, p.surface);
    let mark_x = l.margin.saturating_add(6);
    let mark_y = (l.status / 2).max(5);
    fb.fill_circle(mark_x, mark_y, 5, p.accent);
    device_mode::draw_text(
        fb,
        mark_x.saturating_add(11),
        mark_y.saturating_sub(3),
        b"FILE EDIT VIEW",
        1,
        p.text,
    );
    text_right(
        fb,
        w.saturating_sub(l.margin),
        mark_y.saturating_sub(3),
        b"12:30",
        1,
        p.muted,
    );
    let dock_h = (h / 9).clamp(36, 56);
    let dock_w = 340.min(w.saturating_sub(l.margin * 2));
    let dx = (w - dock_w) / 2;
    let dy = h.saturating_sub(dock_h);
    fb.fill_rect(dx, dy, dock_w, dock_h, p.surface_hi);
    let tile = 30u32;
    let gap = (dock_w.saturating_sub(5 * tile)) / 6;
    for i in 0..5u32 {
        let x = dx + gap + i * (tile + gap);
        fb.fill_rect(
            x,
            dy + (dock_h - tile) / 2,
            tile,
            tile,
            if i == 0 { p.accent } else { p.muted },
        );
    }
    let pane = device_mode::content_pane(mode, l, w, h);
    if pane.2 > 160 && pane.3 > 80 {
        let lights = [0x00ff_5f57u32, 0x00fe_bc2e, 0x0028_c840];
        for (i, color) in lights.iter().enumerate() {
            fb.fill_circle(pane.0 + 14 + i as u32 * 16, pane.1 + 13, 5, *color);
        }
    }
}

/// Console: no desktop chrome at all -- a terminal frame with the
/// identity's mark in the status strip and the wordmark beside it.
fn paint_console(fb: &mut Framebuffer, l: &device_mode::Layout) {
    let p = Platform::Console.palette();
    let w = fb.width;
    let size = 10.min(l.status.saturating_sub(4)).max(6);
    let x = w.saturating_sub(l.margin + size);
    let y = (l.status / 2).saturating_sub(size / 2);
    fb.fill_rect(x, y, size, size, p.accent);
    text_right(
        fb,
        x.saturating_sub(7),
        (l.status / 2).saturating_sub(3),
        b"NYRQIS CONSOLE",
        1,
        p.accent,
    );
}

/// Draws one identity over the chrome `device_mode::render` already
/// drew, reads its own landmarks back, and publishes the result.
pub fn apply(platform: Platform, mode: DeviceMode, fb: &mut Framebuffer) -> Skin {
    let l = device_mode::layout(mode, fb.width, fb.height);
    let p = platform.palette();
    match platform {
        Platform::Nyrqis => paint_nyrqis(fb, &l),
        Platform::Android => paint_android(fb, &l),
        Platform::Windows => paint_windows(fb, mode, &l),
        Platform::MacOs => paint_macos(fb, mode, &l),
        Platform::Console => paint_console(fb, &l),
    }
    let hash = surface_hash(fb);
    let verified = probe(platform, mode, &l, &p, fb);
    CURRENT.store(platform.code(), Ordering::Relaxed);
    Skin {
        platform,
        bar: l.status,
        hash,
        verified,
    }
}

/// Each identity's own invariant, read back through the same mapping:
/// the pixel that only that skin paints. The native identity also pins
/// the device renderer's shared accent block and status strip, because
/// its mark is drawn AROUND them, not over them -- if that ever changes,
/// the boot fails here instead of corrupting the layout contract.
fn probe(
    platform: Platform,
    mode: DeviceMode,
    l: &device_mode::Layout,
    p: &Palette,
    fb: &Framebuffer,
) -> bool {
    let w = fb.width;
    let h = fb.height;
    match platform {
        Platform::Nyrqis => {
            fb.get(w.saturating_sub(26), (l.status / 2).max(5)) == Some(p.accent)
                && fb.get(0, 0) == Some(device_mode::SURFACE)
                && fb.get(l.margin + 2, l.status / 2) == Some(device_mode::PRIMARY)
        }
        Platform::Android => {
            fb.get(0, 0) == Some(p.surface)
                && fb.get(w.saturating_sub(20), (l.status / 2).saturating_sub(1)) == Some(p.accent)
        }
        Platform::Windows => {
            let bar = (h / 8).clamp(28, 48);
            let ty = h.saturating_sub(bar);
            let lx = w / 2 - 9;
            let ly = ty + bar / 2 - 9;
            let pane = device_mode::content_pane(mode, l, w, h);
            fb.get(lx + 2, ly + 2) == Some(p.accent)
                && fb.get(2, h.saturating_sub(2)) == Some(p.surface)
                && (pane.2 <= 200
                    || fb.get(pane.0 + pane.2 - 18, pane.1 + 9) == Some(p.accent))
        }
        Platform::MacOs => {
            let mark_x = l.margin.saturating_add(6);
            let mark_y = (l.status / 2).max(5);
            let dock_h = (h / 9).clamp(36, 56);
            let dock_w = 340.min(w.saturating_sub(l.margin * 2));
            let dx = (w - dock_w) / 2;
            let dy = h.saturating_sub(dock_h);
            let tile = 30u32;
            let gap = dock_w.saturating_sub(5 * tile) / 6;
            let pane = device_mode::content_pane(mode, l, w, h);
            fb.get(mark_x, mark_y) == Some(p.accent)
                && fb.get(0, 0) == Some(p.surface)
                && fb.get(dx + gap + 2, dy + (dock_h - tile) / 2 + 2) == Some(p.accent)
                && (pane.2 <= 160 || fb.get(pane.0 + 14, pane.1 + 13) == Some(0x00ff_5f57))
        }
        Platform::Console => {
            let size = 10.min(l.status.saturating_sub(4)).max(6);
            let x = w.saturating_sub(l.margin + size);
            fb.get(x, l.status / 2) == Some(p.accent)
        }
    }
}

/// Runtime switch: repaints the device chrome (so no pixel of the old
/// posture can survive), then applies the requested identity. This is
/// the public switch the hotkey window and a future session owner use.
pub fn switch(fb: &mut Framebuffer, mode: DeviceMode, platform: Platform) -> Skin {
    let _ = device_mode::render(mode, fb);
    apply(platform, mode, fb)
}

/// The `0x84` gate's switch: renders onto the BOUND surface through a
/// fresh handle (the boot session's handle belongs to `kernel_main`, and
/// a trap-entered gate must not need it), at the form factor the machine
/// booted. Errors are the gate's own errno names: `-ENODEV` when this
/// machine has no bound display, `-EIO` when the skin failed its own
/// read-back -- never a silent success.
pub fn switch_bound(platform: Platform) -> Result<Skin, i64> {
    const ENODEV: i64 = -19;
    const EIO: i64 = -5;
    let Some(mut fb) = (unsafe { crate::devices::framebuffer::from_bound() }) else {
        return Err(ENODEV);
    };
    let skin = switch(&mut fb, crate::device_mode::current(), platform);
    if skin.verified {
        Ok(skin)
    } else {
        Err(EIO)
    }
}

/// The `0x84` form-factor request: re-render the DEVICE chrome at `mode`
/// (its own read-back must verify), re-apply the current identity on top
/// of it (its landmark must verify too), and only then publish the new
/// mode. A request that renders but does not verify changes nothing --
/// the same rule the identity switch follows.
pub fn switch_mode_bound(mode: DeviceMode) -> Result<(), i64> {
    const ENODEV: i64 = -19;
    const EIO: i64 = -5;
    let Some(mut fb) = (unsafe { crate::devices::framebuffer::from_bound() }) else {
        return Err(ENODEV);
    };
    let rendered = crate::device_mode::render(mode, &mut fb);
    if !rendered.probes_ok {
        return Err(EIO);
    }
    let skin = apply(current(), mode, &mut fb);
    if !skin.verified {
        return Err(EIO);
    }
    crate::device_mode::set_current(mode);
    Ok(())
}

/// The boot proof: render every identity, check each one's read-back,
/// assert the five surfaces are genuinely different pictures, then
/// restore the selected identity. `true` only when all of that holds.
pub fn cycle_self_test(fb: &mut Framebuffer, mode: DeviceMode, chosen: Platform) -> bool {
    let mut hashes = [0u32; 5];
    let mut ok = true;
    for (i, platform) in Platform::ALL.iter().enumerate() {
        let skin = switch(fb, mode, *platform);
        hashes[i] = skin.hash;
        ok &= skin.verified;
        serial_println!(
            "  platform: cycle -- {} applied, bar {}px, surface hash {:#010x}, read-back {}",
            platform.as_str(),
            skin.bar,
            skin.hash,
            if skin.verified { "verified" } else { "MISMATCH" }
        );
    }
    let mut unique = 0usize;
    for i in 0..hashes.len() {
        if !hashes[..i].contains(&hashes[i]) {
            unique += 1;
        }
    }
    let distinct = unique == hashes.len();
    let restored = switch(fb, mode, chosen);
    ok &= restored.verified && distinct;
    serial_println!(
        "  platform: cycle proof -- {} identities rendered, {} distinct surface hashes, read-back verified, session restored to {} -- {}",
        Platform::ALL.len(),
        unique,
        chosen.as_str(),
        if ok { "OK" } else { "FAILED" }
    );
    ok
}

/// The runtime-switch session: a bounded window letting a human move
/// between identities with keys 1..5. Every switch goes through the same
/// `switch` path (repaint, apply, read back), so the transcript line is
/// evidence, not a claim. Deliberately called AFTER the device transcript
/// -- a listen window between a boot gate's trigger line and the facts it
/// reads is the race `cursor::boot_session`'s own docs warn about.
pub fn hotkey_session(fb: &mut Framebuffer, mode: DeviceMode) -> bool {
    let mut ok = true;
    serial_println!(
        "  platform: hotkeys 1..5 switch identity at runtime ({})",
        "1 nyrqis, 2 android, 3 windows, 4 macos, 5 console"
    );
    let mut seen = crate::keyboard::scancodes_received();
    let start = crate::idt::timer_ticks();
    let mut switches = 0u32;
    // ~1 s at the PIT's 18.2 Hz: enough for a hand on a real keyboard,
    // short enough not to delay a boot gate.
    while crate::idt::timer_ticks() - start < 18 {
        let now = crate::keyboard::scancodes_received();
        if now != seen {
            seen = now;
            let code = crate::keyboard::last_scancode();
            // Make codes only: a release (high bit set) must not switch.
            if code & 0x80 == 0 {
                let index = match code {
                    0x02 => Some(0),
                    0x03 => Some(1),
                    0x04 => Some(2),
                    0x05 => Some(3),
                    0x06 => Some(4),
                    _ => None,
                };
                if let Some(i) = index {
                    let platform = Platform::ALL[i];
                    let skin = switch(fb, mode, platform);
                    ok &= skin.verified;
                    switches += 1;
                    serial_println!(
                        "  platform: key {} switched the session to {} at runtime, surface hash {:#010x}, read-back {}",
                        i + 1,
                        platform.as_str(),
                        skin.hash,
                        if skin.verified { "verified" } else { "MISMATCH" }
                    );
                }
            }
        }
        x86_64::instructions::hlt();
    }
    serial_println!(
        "  platform: {} runtime switch(es) in the hotkey window; session is {}",
        switches,
        current().as_str()
    );
    ok
}
