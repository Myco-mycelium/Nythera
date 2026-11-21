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
    /// The top bar's height -- the strip the identity's badge is drawn in.
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

impl Platform {
    /// The word this identity wears in Tide's top-bar badge: uppercase, the
    /// shell's own voice, one token per persona.
    pub fn wordmark(self) -> &'static [u8] {
        match self {
            Platform::Nyrqis => b"NYRQIS",
            Platform::Android => b"ANDROID",
            Platform::Windows => b"WINDOWS",
            Platform::MacOs => b"MACOS",
            Platform::Console => b"CONSOLE",
        }
    }
}

/// Draws one identity as Tide wears it: the shell's whole screen, with this
/// identity's accent as the shell's one accent and its wordmark as the top bar's
/// badge. The kernel used to paint a persona bar over the device chrome; Tide is
/// the chrome now, so an identity that drew its own strip would be a second shell
/// fighting the first for the same 34 pixels. The read-back is the shell's brand
/// tile: it takes the identity's accent, so a screen that still shows the old
/// accent fails here instead of passing on a name alone.
pub fn apply(platform: Platform, mode: DeviceMode, fb: &mut Framebuffer) -> Skin {
    let p = platform.palette();
    let verified = device_mode::render_identity(mode, fb, platform.wordmark(), p.accent);
    let hash = surface_hash(fb);
    CURRENT.store(platform.code(), Ordering::Relaxed);
    Skin {
        platform,
        bar: crate::shell::TOP as u32,
        hash,
        verified,
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
