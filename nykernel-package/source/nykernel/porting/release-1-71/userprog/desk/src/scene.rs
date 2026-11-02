//! The AndroWin-style desktop (1-55): status bar with notification / quick-settings panels, desktop icons, up to four windows (float / columns / grid layouts, drag, z-order, minimise),
//! dock, start menu with type-to-search, hover highlights. Layout constants are shared by drawing and hit-testing. The independent model is tools/check_desk.py.
use crate::assets::*; use crate::gfx::*;
pub const WHITE: u32 = 0xFFFFFF; pub const TXT: u32 = 0xE2E8F0; pub const DIM: u32 = 0x94A3B8; pub const PURPLE: u32 = 0xC084FC; pub const BLUE: u32 = 0x38BDF8; pub const AMBER: u32 = 0xFBBF24; pub const TEAL: u32 = 0x2DD4BF;
pub const APPC: [u32; 4] = [PURPLE, BLUE, AMBER, TEAL]; pub const APPN: [&str; 4] = ["Console Hub", "System", "Files", "Terminal"];
pub const APPI28: [usize; 4] = [I_SPORTS_ESPORTS_28, I_SETTINGS_28, I_FOLDER_28, I_TERMINAL_28]; pub const APPI24: [usize; 4] = [I_SPORTS_ESPORTS_24, I_SETTINGS_24, I_FOLDER_24, I_TERMINAL_24];
pub const DOCK: (i32, i32, i32, i32) = (337, 700, 350, 58); pub const DOCK_X: [i32; 5] = [376, 443, 493, 540, 590]; pub const DOCK_VIEW: i32 = 652;
pub const START: (i32, i32, i32, i32) = (242, 372, 540, 316); pub const NOTIF: (i32, i32, i32, i32) = (16, 48, 380, 324); pub const QS: (i32, i32, i32, i32) = (664, 48, 344, 340);
pub const DESK_Y: [i32; 4] = [72, 194, 300, 406]; pub const TA: (i32, i32, i32, i32) = (16, 52, 892, 636); pub const FW: i32 = 520; pub const FH: i32 = 430;
pub const NOTIFS: [(&str, &str, &str, u32); 3] = [("Update ready", "Nyrqis 1-55 can be installed", "now", PURPLE), ("Console Hub", "Cloud Stream session ended", "5m", 0x34D399), ("Files", "4 items synced", "1h", AMBER)];
pub const TILES: [&str; 6] = ["Wi-Fi", "Bluetooth", "Controller", "Do not disturb", "Night light", "Airplane"];
pub const TILEI: [usize; 6] = [I_WIFI_20, I_BLUETOOTH_20, I_SPORTS_ESPORTS_20, I_DO_NOT_DISTURB_ON_20, I_NIGHTLIGHT_20, I_AIRPLANEMODE_ACTIVE_20];
pub const NONE: i32 = i32::MIN;
pub const NP: usize = 10;
pub const PROGS: [(&str, &str); 10] = [("hello.exe", "Windows PE"), ("pewin.exe", "Windows PE"), ("pereloc.exe", "Windows PE"), ("bad_mz.exe", "Windows PE"), ("hello.dex", "Android DEX"), ("ops.dex", "Android DEX"), ("calls.dex", "Android DEX"), ("dex_magic.dex", "Android DEX"), ("demo.nygi", "Game image"), ("bad.nygi", "Game image")];
pub struct St { pub ov: u8, pub ws: [u8; 4], pub wx: [i32; 4], pub wy: [i32; 4], pub ww: [i32; 4], pub wh: [i32; 4], pub z: [u8; 4], pub layout: u8, pub clock: [u8; 5], pub mx: i32, pub my: i32, pub hover: i32,
    pub down: bool, pub drag: (u8, i32, i32), pub dism: u8, pub tog: u8, pub br: i32, pub vol: i32, pub q: [u8; 24], pub qn: usize, pub th: [[u8; 48]; 24], pub thl: [u8; 24], pub thn: usize, pub ti: [u8; 30], pub til: usize,
    pub fmode: u8, pub fscr: i32, pub fin: [u8; 15], pub fil: usize, pub nn: usize, pub names: [[u8; 16]; 20], pub sizes: [i32; 20], pub ready: bool, pub cur: usize, pub buf: [u8; 2016], pub blen: usize, pub dirty: bool, pub fmsg: u8,
    pub ares: [i32; 10], pub anf: [i32; 10], pub apl: [i32; 10], pub arun: i8, pub pout: u64, pub aret: [i32; 10], pub aok: u16, pub tr: i8, pub tp: i8, pub hmode: u8, pub pn: u8, pub pl: [[u8; 12]; 16], pub want: i16, pub ascr: i32, pub sysok: bool, pub sysv: [u64; 3], pub aun: i32, pub abrk: i32 }
pub const fn new_state() -> St { St { ov: 0, ws: [1, 0, 0, 0], wx: [70, 106, 142, 178], wy: [78, 108, 138, 168], ww: [FW; 4], wh: [FH; 4], z: [0, 1, 2, 3], layout: 0, clock: *b"--:--", mx: 512, my: 384, hover: -1, down: false, drag: (0, 0, 0),
    dism: 0, tog: 7, br: 80, vol: 60, q: [0; 24], qn: 0, th: [[0; 48]; 24], thl: [0; 24], thn: 0, ti: [0; 30], til: 0,
    fmode: 0, fscr: 0, fin: [0; 15], fil: 0, nn: 0, names: [[0; 16]; 20], sizes: [0; 20], ready: false, cur: 0, buf: [0; 2016], blen: 0, dirty: false, fmsg: 0,
    ares: [NONE; 10], anf: [0; 10], apl: [0; 10], arun: -1, pout: 0, aret: [0; 10], aok: 0, tr: -1, tp: 0, hmode: 0, pn: 0, pl: [[0; 12]; 16], want: -1, ascr: 0, sysok: false, sysv: [0; 3], aun: 0, abrk: 0 } }
pub fn inside(x: i32, y: i32, r: (i32, i32, i32, i32)) -> bool { x >= r.0 && y >= r.1 && x < r.0 + r.2 && y < r.1 + r.3 }
fn within(x: i32, y: i32, cx: i32, cy: i32, h: i32) -> bool { (x - cx).abs() <= h && (y - cy).abs() <= h }
pub fn nopen(s: &St) -> i32 { s.ws.iter().filter(|&&v| v == 1).count() as i32 }
pub fn wrect(s: &St, i: usize) -> (i32, i32, i32, i32) {
    if s.layout == 0 { return (s.wx[i], s.wy[i], s.ww[i], s.wh[i]); }
    let n = nopen(s); let k = (0..i).filter(|&j| s.ws[j] == 1).count() as i32;
    if s.layout == 1 { let cw = (TA.2 - 12 * (n - 1)) / n; return (TA.0 + k * (cw + 12), TA.1, cw, TA.3); }
    let cols = if n == 1 { 1 } else { 2 }; let rows = (n + cols - 1) / cols; let ch = (TA.3 - 12 * (rows - 1)) / rows; let cw = (TA.2 - 12 * (cols - 1)) / cols; let (r, c) = (k / cols, k % cols);
    let span = n > 1 && n % 2 == 1 && k == n - 1; (TA.0 + c * (cw + 12), TA.1 + r * (ch + 12), if span { TA.2 } else { cw }, ch)
}
pub fn matches(s: &St) -> ([u8; 4], usize) {                   // pinned apps whose name contains the typed query (ASCII, case-insensitive)
    let mut out = [0u8; 4]; let mut n = 0;
    for i in 0..4 { let nm = APPN[i].as_bytes(); let q = &s.q[..s.qn]; let mut ok = q.is_empty();
        if !ok { for st in 0..nm.len() { if st + q.len() <= nm.len() && (0..q.len()).all(|j| nm[st + j].to_ascii_lowercase() == q[j]) { ok = true; break; } } }
        if ok { out[n] = i as u8; n += 1; } }
    (out, n)
}
pub fn qs_val(x: i32) -> i32 { ((x - (QS.0 + 56)) * 100 / 236).clamp(0, 100) }
fn notif_slot(s: &St, i: usize) -> Option<usize> { if s.dism >> i & 1 == 1 { None } else { Some((0..i).filter(|&j| s.dism >> j & 1 == 0).count()) } }
pub fn ncount(s: &St) -> u32 { 3 - (s.dism & 7).count_ones() }
/// ids: 1 dock grid, 6 dock layout button, 10+i dock app, 50+i desktop icon, 60 notification pill, 61 quick-settings cluster, 100+10*i+part window i (0 body, 1 close, 2 minimise, 3 layout, 4 title),
/// start: 30+i pinned app (app index), 40 power, 41 settings, 42 search box, 43 all apps, 99 panel; notifications: 62+i dismiss, 65 clear all, 66 panel; quick settings: 70+i tile, 76/77 sliders, 78 panel
pub fn hit(s: &St, x: i32, y: i32) -> i32 {
    match s.ov {
        1 => if inside(x, y, START) { let (m, n) = matches(s); for j in 0..n { if within(x, y, START.0 + 71 + 126 * j as i32, START.1 + 150, 36) { return 30 + m[j] as i32; } }
            if inside(x, y, (START.0 + 24, START.1 + 24, 492, 40)) { return 42; } if x >= START.0 + 540 - 110 && y >= START.1 + 86 && y < START.1 + 108 && x < START.0 + 540 - 20 { return 43; }
            if within(x, y, START.0 + 540 - 80, START.1 + 316 - 38, 18) { return 41; } if within(x, y, START.0 + 540 - 38, START.1 + 316 - 38, 18) { return 40; } return 99; },
        2 => if inside(x, y, NOTIF) { for i in 0..3 { if let Some(sl) = notif_slot(s, i) { if within(x, y, NOTIF.0 + 12 + 356 - 22, NOTIF.1 + 56 + sl as i32 * 72 + 32, 14) { return 62 + i as i32; } } }
            if inside(x, y, (NOTIF.0 + 380 - 100, NOTIF.1 + 10, 90, 30)) { return 65; } return 66; },
        3 => if inside(x, y, QS) { for i in 0..6 { if inside(x, y, (QS.0 + 16 + (i as i32 % 2) * 160, QS.1 + 16 + (i as i32 / 2) * 72, 152, 64)) { return 70 + i as i32; } }
            if inside(x, y, (QS.0 + 48, QS.1 + 264 - 14, 252, 28)) { return 76; } if inside(x, y, (QS.0 + 48, QS.1 + 304 - 14, 252, 28)) { return 77; } return 78; },
        _ => {} }
    if inside(x, y, (16, 4, 170, 32)) { return 60; } if inside(x, y, (786, 4, 224, 32)) { return 61; }
    if inside(x, y, DOCK) { if within(x, y, DOCK_X[0], 729, 22) { return 1; } for i in 0..4 { if within(x, y, DOCK_X[i + 1], 729, 22) { return 10 + i as i32; } } if within(x, y, DOCK_VIEW, 729, 22) { return 6; } return 99; }
    for k in (0..4).rev() { let i = s.z[k] as usize; if s.ws[i] != 1 { continue; } let r = wrect(s, i); if !inside(x, y, r) { continue; }
        let b = 100 + 10 * i as i32; if within(x, y, r.0 + r.2 - 28, r.1 + 20, 12) { return b + 1; } if within(x, y, r.0 + r.2 - 62, r.1 + 20, 12) { return b + 2; }
        if within(x, y, r.0 + r.2 - 89, r.1 + 20, 12) { return b + 3; } if s.layout == 0 && x >= r.0 + r.2 - 18 && y >= r.1 + r.3 - 18 { return b + 5; } if i == 0 { let f = ahit(s, r, x, y); if f != -1 { return f; } } if i == 2 { let f = fhit(s, r, x, y); if f != -1 { return f; } } return if y < r.1 + 40 { b + 4 } else { b }; }
    for i in 0..4 { if inside(x, y, (924, DESK_Y[i], 56, 56)) { return 50 + i as i32; } }
    -1
}
fn hv(s: &St, id: i32) -> bool { s.hover == id }
pub const FMSG: [&str; 9] = ["", "Saved", "Created", "Deleted", "A file with that name exists", "Directory full (20 files)", "Bad name", "Storage error", "Too large (2016 bytes max)"];
pub fn frows(h: i32) -> i32 { ((h - 98 - 12) / 38).max(1) }
pub fn vrows(h: i32) -> i32 { ((h - 96 - 14 - 14) / 20).max(1) }
pub fn nlines(s: &St) -> i32 { 1 + s.buf[..s.blen].iter().filter(|&&c| c == b'\n').count() as i32 }
/// the i-th line of the open file, printable ASCII only (anything else shows as '?'), at most 110 characters
pub fn vline(s: &St, i: i32, out: &mut [u8; 110]) -> usize {
    let mut ln = 0; let mut st = 0; let d = &s.buf[..s.blen]; let mut k = 0;
    while k <= d.len() { if k == d.len() || d[k] == b'\n' { if ln == i { let mut n = 0; for &c in &d[st..k] { if n < 110 { out[n] = if (32..127).contains(&c) { c } else { b'?' }; n += 1; } } return n; } ln += 1; st = k + 1; } k += 1; }
    0
}
pub fn itoa(mut n: i32, o: &mut [u8; 12]) -> usize { if n < 0 { o[0] = b'?'; return 1; } if n == 0 { o[0] = b'0'; return 1; } let mut t = [0u8; 12]; let mut l = 0; while n > 0 { t[l] = b'0' + (n % 10) as u8; n /= 10; l += 1; } for i in 0..l { o[i] = t[l - 1 - i]; } l }
/// the pointer / USB part of the System page's first row, also printed by the terminal's `sys`
pub fn kp_text<'a>(sv0: u64, out: &'a mut [u8; 48]) -> &'a str {
    let pb = sv0 >> 24;
    let ptr: &[u8] = if pb & 1 == 0 { b"mouse n/a" } else if pb & 2 != 0 { b"PS/2 mouse ok" } else { b"PS/2 mouse no ACK" };
    let mut ub = [0u8; 40]; let mut un = 0; for (k, nm) in [&b"UHCI"[..], b"OHCI", b"EHCI", b"xHCI"].iter().enumerate() { if (pb >> (2 + k)) & 1 != 0 { if un > 0 { ub[un] = b'+'; un += 1; } for &c in nm.iter() { ub[un] = c; un += 1; } } }
    let usbt: &[u8] = if un == 0 { b"no USB controller" } else { &ub[..un] }; let nodrv: &[u8] = if un == 0 { b"" } else if pb & 64 != 0 { b" (HID ok)" } else { b" (no driver)" };
    cat(out, &[b"Nyrqis 1-71 | ", ptr, b" | ", usbt, nodrv])
}
pub fn cat<'a>(out: &'a mut [u8; 48], parts: &[&[u8]]) -> &'a str { let mut n = 0; for p in parts { for &c in p.iter() { if n < 48 { out[n] = c; n += 1; } } } core::str::from_utf8(&out[..n]).unwrap_or("") }
pub fn killable(r: &[u8; 12]) -> bool { r[4..].starts_with(b"peload") || r[4..].starts_with(b"dexload") }
fn ahit(s: &St, r: (i32, i32, i32, i32), x: i32, y: i32) -> i32 {
    if y < r.1 + 41 { return -1; }
    if inside(x, y, (r.0 + 24, r.1 + 52, 92, 30)) { return 430; } if inside(x, y, (r.0 + 124, r.1 + 52, 100, 30)) { return 431; }
    if s.hmode == 1 { if inside(x, y, (r.0 + r.2 - 100, r.1 + 52, 76, 30)) { return 432; }
        for k in 0..frows(r.3).min(16) { if k >= s.pn as i32 { break; } if killable(&s.pl[k as usize]) && inside(x, y, (r.0 + r.2 - 24 - 12 - 62, r.1 + 98 + 38 * k + 5, 62, 24)) { return 440 + k; } } return -1; }
    let nr = frows(r.3).min(10);
    if nr < 10 { if within(x, y, r.0 + r.2 - 60, r.1 + 67, 14) { return 410; } if within(x, y, r.0 + r.2 - 28, r.1 + 67, 14) { return 411; } }
    for k in 0..nr { let a = s.ascr + k; if a >= 10 { break; } if inside(x, y, (r.0 + 24, r.1 + 98 + 38 * k, r.2 - 48, 34)) { return 400 + a; } } -1
}
fn fhit(s: &St, r: (i32, i32, i32, i32), x: i32, y: i32) -> i32 {
    if y < r.1 + 41 || !s.ready { return -1; }
    match s.fmode {
        0 => { if inside(x, y, (r.0 + 24, r.1 + 52, 112, 30)) { return 330; } let nr = frows(r.3);
            if s.nn as i32 > nr { if within(x, y, r.0 + r.2 - 60, r.1 + 67, 14) { return 321; } if within(x, y, r.0 + r.2 - 28, r.1 + 67, 14) { return 322; } }
            for k in 0..nr { let a = s.fscr + k; if a >= s.nn as i32 { break; } let ry = r.1 + 98 + 38 * k; if within(x, y, r.0 + r.2 - 44, ry + 17, 14) { return 360 + a; } if inside(x, y, (r.0 + 24, ry, r.2 - 48, 34)) { return 340 + a; } } -1 }
        1 => { if inside(x, y, (r.0 + 24, r.1 + 52, 76, 30)) { return 320; } if inside(x, y, (r.0 + r.2 - 100, r.1 + 52, 76, 30)) { return 323; }
            if within(x, y, r.0 + r.2 - 40, r.1 + 110, 14) { return 321; } if within(x, y, r.0 + r.2 - 40, r.1 + r.3 - 28, 14) { return 322; } -1 }
        _ => -1 }
}
fn hl(b: &mut Buf, on: bool, cx: i32, cy: i32, hs: i32, r: i32) { if on { rrect(b, cx - hs, cy - hs, hs * 2, hs * 2, r, WHITE, WHITE, 24, WHITE, 0); } }
pub fn draw(b: &mut Buf, s: &St) {
    // status bar
    rect(b, 0, 0, 1024, 40, 0x000000, 110); rect(b, 0, 40, 1024, 1, WHITE, 18);
    if hv(s, 60) || s.ov == 2 { rrect(b, 16, 4, 170, 32, 16, WHITE, WHITE, 16, WHITE, 0); } if hv(s, 61) || s.ov == 3 { rrect(b, 786, 4, 224, 32, 16, WHITE, WHITE, 16, WHITE, 0); }
    icon(b, I_NOTIFICATIONS_18, 27, 11, TXT, 255);
    if ncount(s) > 0 { rrect(b, 56, 10, 20, 20, 10, 0x4F46E5, 0x4F46E5, 255, WHITE, 0); text_c(b, M11, 66, 24, ["0", "1", "2", "3"][ncount(s) as usize], WHITE, 255); }
    text(b, M13, 82, 25, "Notifications", TXT, 255); icon(b, I_KEYBOARD_ARROW_DOWN_18, 166, 11, DIM, 255);
    rrect(b, 474, 18, 36, 4, 2, WHITE, WHITE, 60, WHITE, 0);
    icon(b, I_WIFI_16, 800, 12, 0x34D399, if s.tog & 1 == 1 { 255 } else { 70 }); icon(b, I_BLUETOOTH_16, 826, 12, BLUE, if s.tog >> 1 & 1 == 1 { 255 } else { 70 }); icon(b, I_SPORTS_ESPORTS_16, 850, 12, PURPLE, if s.tog >> 2 & 1 == 1 { 255 } else { 70 });
    icon(b, I_BATTERY_CHARGING_FULL_16, 874, 12, 0x34D399, 255); text(b, M12, 896, 25, "88%", TXT, 255); rect(b, 926, 13, 1, 14, WHITE, 50);
    text(b, M13, 938, 25, core::str::from_utf8(&s.clock).unwrap_or("--:--"), WHITE, 255); icon(b, I_KEYBOARD_ARROW_DOWN_18, 984, 11, DIM, 255);
    // desktop icons
    for i in 0..4 { let y = DESK_Y[i]; let h = hv(s, 50 + i as i32); rrect(b, 924, y, 56, 56, 14, WHITE, WHITE, if h { 26 } else { 12 }, WHITE, if h { 60 } else { 22 }); icon(b, APPI28[i], 938, y + 14, APPC[i], 255);
        let w = text_w(M12, APPN[i]) + 20; rrect(b, 952 - w / 2, y + 62, w, 22, 11, 0x000000, 0x000000, 110, WHITE, 0); text_c(b, M12, 952, y + 77, APPN[i], TXT, 255); }
    for k in 0..4 { let i = s.z[k] as usize; if s.ws[i] == 1 { window(b, s, i, k == 3 || (k..4).all(|j| s.ws[s.z[j] as usize] != 1 || j == k)); } }
    // dock
    rrect(b, DOCK.0, DOCK.1, DOCK.2, DOCK.3, 29, 0x0F1220, 0x0F1220, 205, WHITE, 24);
    for (id, cx) in [(1, DOCK_X[0]), (10, DOCK_X[1]), (11, DOCK_X[2]), (12, DOCK_X[3]), (13, DOCK_X[4]), (6, DOCK_VIEW)] { hl(b, hv(s, id), cx, 729, 22, 12); }
    icon(b, I_GRID_VIEW_24, DOCK_X[0] - 12, 717, BLUE, 255); rect(b, 411, 715, 1, 28, WHITE, 40);
    for i in 0..4 { icon(b, APPI24[i], DOCK_X[i + 1] - 12, 717, APPC[i], 255);
        if s.ws[i] == 1 { rrect(b, DOCK_X[i + 1] - 3, 750, 6, 6, 3, APPC[i], APPC[i], 255, WHITE, 0); } else if s.ws[i] == 2 { rrect(b, DOCK_X[i + 1] - 2, 751, 4, 4, 2, APPC[i], APPC[i], 110, WHITE, 0); } }
    rect(b, 621, 715, 1, 28, WHITE, 40); icon(b, I_VIEW_QUILT_18, DOCK_VIEW - 9, 720, if s.layout > 0 { PURPLE } else { DIM }, 255);
    match s.ov { 1 => start(b, s), 2 => notif(b, s), 3 => qs(b, s), _ => {} }
    clear_clip(); if s.tog >> 4 & 1 == 1 { rect(b, 0, 0, 1024, 768, 0xFF9628, 28); }          // night light: a warm veil; brightness: a black veil, alpha = 100 - level
    if s.br < 100 { rect(b, 0, 0, 1024, 768, 0x000000, (100 - s.br) as u32); }
}
fn procs(b: &mut Buf, s: &St, x: i32, y: i32, w: i32, h: i32) {
    pill(b, x + w - 100, y + 52, 76, false, hv(s, 432), PURPLE, "Refresh");
    if s.pn == 0 { text_c(b, R13, x + w / 2, y + 150, "No snapshot yet. Press Refresh.", DIM, 255); }
    for k in 0..frows(h).min(16) { if k >= s.pn as i32 { break; } let r = &s.pl[k as usize]; let ry = y + 98 + 38 * k;
        rrect(b, x + 24, ry, w - 48, 34, 10, WHITE, WHITE, 8, WHITE, 20);
        let mut ib = [0u8; 12]; let il = itoa(r[0] as i32, &mut ib); let mut e = 8; for q in 0..8 { if r[4 + q] == 0 { e = q; break; } }
        let nm: &[u8] = if r[4] == 0 { b"root" } else { &r[4..4 + e] }; let mut sb = [0u8; 48]; let t = cat(&mut sb, &[&ib[..il], b"  ", nm]); text(b, M14, x + 40, ry + 22, t, WHITE, 255);
        let mut cb = [0u8; 12]; let cl = itoa(r[1] as i32, &mut cb); let mut pb = [0u8; 12]; let pl = itoa(r[3] as i32, &mut pb); let mut sb2 = [0u8; 48];
        let t2 = cat(&mut sb2, &[b"C", &cb[..cl], b"  ", &pb[..pl], b"pg", if r[2] != 0 { b" blk" } else { b"" }]); if w >= 480 { text(b, R12, x + 200, ry + 22, t2, DIM, 255); }
        if killable(r) { let rx = x + w - 24 - 12 - 62; let hot = hv(s, 440 + k); rrect(b, rx, ry + 5, 62, 24, 12, 0xF87171, 0xF87171, if hot { 140 } else { 70 }, 0xF87171, 120); text_c(b, M12, rx + 31, ry + 21, "Kill", WHITE, 255); } }
}
fn pill(b: &mut Buf, x: i32, y: i32, w: i32, on: bool, hot: bool, c: u32, label: &str) {
    if on { rrect(b, x, y, w, 30, 15, c, c, if hot { 140 } else { 90 }, c, 150); text_c(b, M13, x + w / 2, y + 20, label, WHITE, 255); }
    else { rrect(b, x, y, w, 30, 15, WHITE, WHITE, if hot { 26 } else { 12 }, WHITE, 40); text_c(b, M13, x + w / 2, y + 20, label, if hot { TXT } else { DIM }, 255); }
}
fn files(b: &mut Buf, s: &St, x: i32, y: i32, w: i32, h: i32) {
    if !s.ready { text_c(b, R13, x + w / 2, y + h / 2, "Storage service unavailable", DIM, 255); return; }
    let mut sb = [0u8; 48];
    match s.fmode {
        0 => { pill(b, x + 24, y + 52, 112, true, hv(s, 330), PURPLE, "+ New file");
            let mut nb = [0u8; 12]; let nl = itoa(s.nn as i32, &mut nb); let t = cat(&mut sb, &[&nb[..nl], b" of 20 files"]); if w >= 420 { text(b, R12, x + 152, y + 72, t, DIM, 255); }
            let nr = frows(h);
            if s.nn as i32 > nr { for (cx, up) in [(x + w - 60, true), (x + w - 28, false)] { rrect(b, cx - 14, y + 53, 28, 28, 8, WHITE, WHITE, if hv(s, if up { 321 } else { 322 }) { 26 } else { 10 }, WHITE, 0); icon(b, if up { I_KEYBOARD_ARROW_UP_18 } else { I_KEYBOARD_ARROW_DOWN_18 }, cx - 9, y + 58, TXT, 255); } }
            if s.nn == 0 { text_c(b, R13, x + w / 2, y + 150, "No files yet. Create one with + New file.", DIM, 255); }
            for k in 0..nr { let a = s.fscr + k; if a >= s.nn as i32 { break; } let ai = a as usize; let ry = y + 98 + 38 * k;
                rrect(b, x + 24, ry, w - 48, 34, 10, WHITE, WHITE, if hv(s, 340 + a) { 26 } else { 8 }, WHITE, 20); icon(b, I_DESCRIPTION_24, x + 36, ry + 5, BLUE, 255);
                let nm = core::str::from_utf8(&s.names[ai][..crate::nfs::nlen(&s.names[ai])]).unwrap_or(""); text(b, M14, x + 72, ry + 22, nm, WHITE, 255);
                let mut zb = [0u8; 12]; let zl = itoa(s.sizes[ai], &mut zb); let t = cat(&mut sb, &[&zb[..zl], b" bytes"]); if w >= 360 { text(b, R12, x + w - 150, ry + 22, t, DIM, 255); }
                hl(b, hv(s, 360 + a), x + w - 44, ry + 17, 14, 8); icon(b, I_CLOSE_18, x + w - 53, ry + 8, if hv(s, 360 + a) { WHITE } else { DIM }, 255); }
            if s.fmsg != 0 { text(b, R12, x + 24, y + h - 12, FMSG[s.fmsg as usize], if s.fmsg >= 4 { 0xF87171 } else { TEAL }, 255); } }
        1 => { rrect(b, x + 24, y + 52, 76, 30, 15, WHITE, WHITE, if hv(s, 320) { 26 } else { 12 }, WHITE, 40); icon(b, I_ARROW_BACK_18, x + 34, y + 58, TXT, 255); text(b, M13, x + 58, y + 72, "Back", TXT, 255);
            let nm = core::str::from_utf8(&s.names[s.cur][..crate::nfs::nlen(&s.names[s.cur])]).unwrap_or(""); let nw = text(b, M13, x + 112, y + 72, nm, WHITE, 255);
            if s.dirty { text(b, M13, x + 112 + nw + 6, y + 72, "*", TEAL, 255); } else if s.fmsg != 0 { text(b, R12, x + 112 + nw + 12, y + 72, FMSG[s.fmsg as usize], if s.fmsg >= 4 { 0xF87171 } else { TEAL }, 255); }
            pill(b, x + w - 100, y + 52, 76, s.dirty, hv(s, 323), TEAL, "Save");
            rrect(b, x + 24, y + 96, w - 48, h - 110, 10, 0x000000, 0x000000, 120, WHITE, 16);
            let nr = vrows(h); let nl = nlines(s);
            set_clip(x + 26, y + 98, w - 52 - 40, h - 114);
            for k in 0..nr { let li = s.fscr + k; if li >= nl { break; } let mut lb = [0u8; 110]; let n = vline(s, li, &mut lb); let t = core::str::from_utf8(&lb[..n]).unwrap_or(""); let base = y + 118 + 20 * k;
                let tw = text(b, R13, x + 36, base, t, TXT, 255); if li == nl - 1 { rect(b, x + 37 + tw, base - 12, 2, 15, TEAL, 255); } }
            set_clip(x + 1, y + 1, w - 2, h - 2);
            for (cy, up) in [(y + 110, true), (y + h - 28, false)] { rrect(b, x + w - 54, cy - 14, 28, 28, 8, WHITE, WHITE, if hv(s, if up { 321 } else { 322 }) { 26 } else { 10 }, WHITE, 0); icon(b, if up { I_KEYBOARD_ARROW_UP_18 } else { I_KEYBOARD_ARROW_DOWN_18 }, x + w - 49, cy - 9, TXT, 255); } }
        _ => { rrect(b, x + 24, y + 52, w - 48, 32, 16, WHITE, WHITE, 10, PURPLE, 150); let lw = text(b, M13, x + 40, y + 73, "New file name:", DIM, 255);
            let nm = core::str::from_utf8(&s.fin[..s.fil]).unwrap_or(""); let tw = text(b, M13, x + 40 + lw + 8, y + 73, nm, WHITE, 255); rect(b, x + 40 + lw + 9 + tw, y + 60, 2, 16, TEAL, 255);
            text(b, R12, x + 24, y + 112, "Letters only, up to 15. Enter creates the file, Esc cancels.", DIM, 255);
            if s.fmsg != 0 { text(b, R12, x + 24, y + 136, FMSG[s.fmsg as usize], 0xF87171, 255); } }
    }
}
fn window(b: &mut Buf, s: &St, app: usize, focused: bool) {
    let (x, y, w, h) = wrect(s, app); clear_clip();
    rrect(b, x - 6, y - 4, w + 12, h + 14, 22, 0x000000, 0x000000, if focused { 80 } else { 40 }, WHITE, 0); rrect(b, x, y, w, h, 16, 0x0D1020, 0x090B18, 242, WHITE, if focused { 44 } else { 22 });
    set_clip(x + 1, y + 1, w - 2, h - 2);
    rrect(b, x + 1, y + 1, w - 2, 40, 15, WHITE, WHITE, 8, WHITE, 0); rect(b, x + 1, y + 24, w - 2, 17, 0x0D1020, 0); rect(b, x + 1, y + 40, w - 2, 1, WHITE, 22);
    text(b, M13, x + 18, y + 25, APPN[app], TXT, 255); let bid = 100 + 10 * app as i32;
    hl(b, hv(s, bid + 3), x + w - 89, y + 20, 14, 8); hl(b, hv(s, bid + 2), x + w - 62, y + 20, 14, 8);
    if hv(s, bid + 1) { rrect(b, x + w - 42, y + 6, 28, 28, 8, 0xEF4444, 0xEF4444, 200, WHITE, 0); }
    icon(b, I_VIEW_QUILT_18, x + w - 98, y + 11, if s.layout > 0 { PURPLE } else { DIM }, 255); icon(b, I_REMOVE_18, x + w - 71, y + 11, DIM, 255); icon(b, I_CLOSE_18, x + w - 37, y + 11, TXT, 255);
    match app {
        0 => { pill(b, x + 24, y + 52, 92, s.hmode == 0, hv(s, 430), PURPLE, "Programs"); pill(b, x + 124, y + 52, 100, s.hmode == 1, hv(s, 431), PURPLE, "Processes");
            if s.hmode == 1 { procs(b, s, x, y, w, h); } else { let nr = frows(h).min(10);
            if nr < 10 { for (cx, up) in [(x + w - 60, true), (x + w - 28, false)] { rrect(b, cx - 14, y + 53, 28, 28, 8, WHITE, WHITE, if hv(s, if up { 410 } else { 411 }) { 26 } else { 10 }, WHITE, 0); icon(b, if up { I_KEYBOARD_ARROW_UP_18 } else { I_KEYBOARD_ARROW_DOWN_18 }, cx - 9, y + 58, TXT, 255); } }
            for k in 0..nr { let a = s.ascr + k; if a >= 10 { break; } let ai = a as usize; let ry = y + 98 + 38 * k; let hot = hv(s, 400 + a);
                rrect(b, x + 24, ry, w - 48, 34, 10, WHITE, WHITE, if hot { 26 } else { 8 }, WHITE, 20); icon(b, if ai < 4 { I_GRID_VIEW_24 } else if ai < 8 { I_SPORTS_ESPORTS_24 } else { I_FOLDER_24 }, x + 36, ry + 5, if ai < 4 { BLUE } else if ai < 8 { 0x34D399 } else { AMBER }, 255);
                text(b, M14, x + 72, ry + 22, PROGS[ai].0, WHITE, 255); if w >= 480 { text(b, R12, x + 200, ry + 22, PROGS[ai].1, DIM, 255); }
                let mut sb = [0u8; 48]; let r = s.ares[ai];
                let (t, c): (&str, u32) = if s.arun == a as i8 { ("Launching...", DIM) } else if r == NONE { ("", DIM) } else if ai >= 8 && r == 0 { let (mut fb, mut pb) = ([0u8; 12], [0u8; 12]); let (fl, pl) = (itoa(s.anf[ai], &mut fb), itoa(s.apl[ai], &mut pb)); (cat(&mut sb, &[b"Verified, ", &fb[..fl], b" files, play #", &pb[..pl]]), TEAL) }
                    else if ai >= 8 && r < 0 { let mut nb = [0u8; 12]; let nl = itoa(-r, &mut nb); (cat(&mut sb, &[b"Corrupt (-", &nb[..nl], b")"]), 0xF87171) }
                    else if r == 0 { if s.aok >> ai & 1 != 0 { let mut nb = [0u8; 12]; let nl = itoa(s.aret[ai].wrapping_abs(), &mut nb); (cat(&mut sb, &[if ai < 4 { &b"Loaded, exit code "[..] } else { &b"Loaded, returned "[..] }, if s.aret[ai] < 0 { b"-" } else { b"" }, &nb[..nl]]), TEAL) } else { ("Loaded", TEAL) } } else if r == -20 { ("Refused (-20)", 0xF87171) }
                    else { let mut nb = [0u8; 12]; let nl = itoa(if r < 0 { -r } else { r }, &mut nb); (cat(&mut sb, &[if r < 0 { b"Error -" } else { b"Result " }, &nb[..nl]]), 0xF87171) };
                if !t.is_empty() { text(b, R12, x + w - 24 - 12 - 62 - 12 - text_w(R12, t), ry + 22, t, c, 255); }
                let rx = x + w - 24 - 12 - 62; rrect(b, rx, ry + 5, 62, 24, 12, PURPLE, PURPLE, if hot { 140 } else { 70 }, PURPLE, 120); text_c(b, M12, rx + 31, ry + 21, "Run", WHITE, 255); } } }
        1 => { let mut sb = [0u8; 48]; let sv = s.sysv; let (mode, signer) = ((sv[0] & 255) as u8, ((sv[0] >> 8) & 255) as u8);
            let sbt: &str = if !s.sysok { "waiting for the kernel..." } else { match (mode, signer) { (2, _) => "Disabled by the user -- image NOT verified", (1, 2) => "Enrollment -- user key accepted", (1, _) => "Enrollment -- platform key", (_, 2) => "Enforcing -- user-enrolled key", _ => "Enforcing -- signed by the platform key" } };
            let mut hx = [0u8; 16]; for k in 0..16 { hx[k] = b"0123456789abcdef"[((sv[1] >> (60 - 4 * k)) & 15) as usize]; }
            let mut nb = [0u8; 12]; let nl = itoa(sv[2] as i32, &mut nb);
            let bit: &str = if !s.sysok { "waiting for the kernel..." } else { cat(&mut sb, &[&nb[..nl], b" bytes  SHA-256 ", &hx]) };
            let mut sb2 = [0u8; 48]; let mut fb = [0u8; 12]; let fl = itoa(s.nn as i32, &mut fb);
            let st: &str = if !s.ready { "Storage service unavailable" } else { cat(&mut sb2, &[b"NyFS volume at LBA 1900: ", &fb[..fl], b" of 20 files"]) };
            let (mut run, mut ok) = (0i32, 0i32); for &r in s.ares.iter() { if r != NONE { run += 1; if r == 0 { ok += 1; } } }
            let mut sb3 = [0u8; 48]; let (mut rb, mut ob, mut xb) = ([0u8; 12], [0u8; 12], [0u8; 12]); let (rl, ol, xl) = (itoa(run, &mut rb), itoa(ok, &mut ob), itoa(run - ok, &mut xb));
            let pg: &str = if run == 0 { "none run yet (Console Hub)" } else { cat(&mut sb3, &[&rb[..rl], b" run: ", &ob[..ol], b" loaded, ", &xb[..xl], b" refused"]) };
            let mut sb4 = [0u8; 48]; let ck: &str = cat(&mut sb4, &[&s.clock, b" (CMOS clock, via rtcd)"]);
            let mut sb5 = [0u8; 48]; let (mut gb, mut qb) = ([0u8; 12], [0u8; 12]); let (gl, ql) = (itoa(s.anf[8], &mut gb), itoa(s.apl[8], &mut qb)); let mut kb = [0u8; 12]; let kl = itoa(if s.ares[8] < 0 && s.ares[8] != NONE { -s.ares[8] } else { 0 }, &mut kb);
            let gi: &str = if s.ares[8] == NONE { "none opened yet (Console Hub)" } else if s.ares[8] == 0 { cat(&mut sb5, &[b"demo.nygi: ", &gb[..gl], b" files verified, ", &qb[..ql], b" plays in overlay"]) } else { cat(&mut sb5, &[b"demo.nygi FAILED verification (-", &kb[..kl], b")"]) };
            let mut sb6 = [0u8; 48]; let (mut ab, mut kb2) = ([0u8; 12], [0u8; 12]); let (al, kl2) = (itoa(s.aun, &mut ab), itoa(s.abrk, &mut kb2));
            let au: &str = if !s.ready { "storage unavailable" } else if s.aun == 0 { "no events yet" } else if s.abrk == 0 { cat(&mut sb6, &[&ab[..al], b" events, hash chain intact"]) } else { cat(&mut sb6, &[&ab[..al], b" events, BROKEN at record ", &kb2[..kl2]]) };
            let mut sb7 = [0u8; 48]; let kp: &str = kp_text(sv[0], &mut sb7);
            let rows: [(&str, &str); 8] = [("Secure boot", sbt), ("Boot image (measured)", bit), ("Kernel, pointer, USB", kp), ("Storage", st), ("Programs", pg), ("Game image (.nygi, read-only + overlay)", gi), ("Audit log (hash-chained, file auditlog)", au), ("Time", ck)];
            for (k, r) in rows.iter().enumerate() { let ry = y + 56 + 46 * k as i32; rrect(b, x + 24, ry, w - 48, 40, 10, WHITE, WHITE, 8, WHITE, 20); text(b, R12, x + 40, ry + 16, r.0, DIM, 255); text(b, M13, x + 40, ry + 32, r.1, WHITE, 255); } }
        2 => files(b, s, x, y, w, h),
        _ => { rrect(b, x + 1, y + 41, w - 2, h - 42, 15, 0x000000, 0x000000, 120, WHITE, 0); text(b, R14, x + 20, y + 74, "Nyrqis OS terminal -- type help", DIM, 255);
            let rows = (h - 14 - 98) / 22 + 1; let show = ((rows - 1).max(0) as usize).min(s.thn);
            for k in 0..show { let li = s.thn - show + k; let ln = &s.th[li][..s.thl[li] as usize]; text(b, R14, x + 20, y + 98 + 22 * k as i32, core::str::from_utf8(ln).unwrap_or(""), if ln.starts_with(b"nyrqis@") { TEAL } else { TXT }, 255); }
            let py = y + 98 + 22 * show as i32; let pw = text(b, R14, x + 20, py, "nyrqis@desk:~$ ", TEAL, 255); let iw = text(b, R14, x + 20 + pw, py, core::str::from_utf8(&s.ti[..s.til]).unwrap_or(""), WHITE, 255); rect(b, x + 22 + pw + iw, py - 12, 2, 15, TEAL, 255); }
    }
    if s.layout == 0 { for (dx, dy) in [(6, 4), (10, 4), (14, 4), (6, 8), (10, 8), (6, 12)] { rect(b, x + w - dx - 2, y + h - dy - 2, 2, 2, WHITE, if hv(s, bid + 5) { 220 } else { 90 }); } }
    clear_clip();
}
fn start(b: &mut Buf, s: &St) {
    let (x, y, w, h) = START; rrect(b, x - 8, y - 4, w + 16, h + 18, 30, 0x000000, 0x000000, 80, WHITE, 0); rrect(b, x, y, w, h, 24, 0x161828, 0x10121F, 238, WHITE, 26);
    rrect(b, x + 24, y + 24, w - 48, 40, 20, WHITE, WHITE, 10, WHITE, if s.qn > 0 { 70 } else { 36 }); icon(b, I_SEARCH_20, x + 40, y + 34, DIM, 255);
    if s.qn == 0 { text(b, R13, x + 68, y + 49, "Search apps, games, settings, or web...", DIM, 255); }
    else { let t = core::str::from_utf8(&s.q[..s.qn]).unwrap_or(""); let tw = text(b, R13, x + 68, y + 49, t, WHITE, 255); rect(b, x + 68 + tw + 1, y + 36, 1, 16, WHITE, 255); }
    text(b, M12, x + 28, y + 98, if s.qn == 0 { "PINNED APPS" } else { "RESULTS" }, 0xCBD5E1, 255); text(b, M12, x + w - 28 - text_w(M12, "All Apps  ") - 4, y + 98, "All Apps", 0x818CF8, 255); icon(b, I_CHEVRON_RIGHT_14, x + w - 38, y + 86, 0x818CF8, 255);
    let (m, n) = matches(s);
    for j in 0..n { let i = m[j] as usize; let cx = x + 71 + 126 * j as i32; rrect(b, cx - 24, y + 126, 48, 48, 14, APPC[i], APPC[i], if hv(s, 30 + i as i32) { 80 } else { 40 }, APPC[i], 60); icon(b, APPI24[i], cx - 12, y + 138, APPC[i], 255); text_c(b, M12, cx, y + 196, APPN[i], TXT, 255); }
    if n == 0 { text_c(b, R13, x + w / 2, y + 160, "No results", DIM, 255); }
    rect(b, x + 24, y + 235, w - 48, 1, WHITE, 24);
    rrect(b, x + 26, y + 258, 36, 36, 18, 0x8B5CF6, 0x6D28D9, 255, WHITE, 0); text_c(b, M12, x + 44, y + 281, "NY", WHITE, 255);
    text(b, M13, x + 74, y + 272, "Nyrqis User", WHITE, 255); text(b, R12, x + 74, y + 289, "Nyrqis OS Administrator", DIM, 255);
    hl(b, hv(s, 41), x + w - 80, y + 278, 18, 10); hl(b, hv(s, 40), x + w - 38, y + 278, 18, 10);
    icon(b, I_SETTINGS_20, x + w - 90, y + 268, DIM, 255); icon(b, I_POWER_SETTINGS_NEW_20, x + w - 48, y + 268, 0xA78BFA, 255);
}
fn notif(b: &mut Buf, s: &St) {
    let (x, y, w, h) = NOTIF; rrect(b, x - 8, y - 4, w + 16, h + 18, 30, 0x000000, 0x000000, 80, WHITE, 0); rrect(b, x, y, w, h, 24, 0x161828, 0x10121F, 238, WHITE, 26);
    text(b, M14, x + 20, y + 34, "Notifications", WHITE, 255);
    if ncount(s) > 0 { text(b, M12, x + w - 20 - text_w(M12, "Clear all"), y + 32, "Clear all", if hv(s, 65) { 0xC7D2FE } else { 0x818CF8 }, 255); }
    for i in 0..3 { if let Some(sl) = notif_slot(s, i) { let n = NOTIFS[i]; let iy = y + 56 + sl as i32 * 72; let ix = x + 12;
        rrect(b, ix, iy, 356, 64, 14, WHITE, WHITE, if hv(s, 62 + i as i32) { 14 } else { 8 }, WHITE, 18); rrect(b, ix + 12, iy + 14, 36, 36, 18, n.3, n.3, 60, n.3, 110);
        text(b, M13, ix + 60, iy + 27, n.0, WHITE, 255); text(b, R12, ix + 60, iy + 46, n.1, DIM, 255); text(b, R12, ix + 356 - 64 - text_w(R12, n.2) / 2, iy + 27, n.2, DIM, 255);
        hl(b, hv(s, 62 + i as i32), ix + 356 - 22, iy + 32, 14, 8); icon(b, I_CLOSE_14, ix + 356 - 29, iy + 25, DIM, 255); } }
    if ncount(s) == 0 { text_c(b, R13, x + w / 2, y + 170, "You're all caught up", DIM, 255); }
}
fn qs(b: &mut Buf, s: &St) {
    let (x, y, w, h) = QS; rrect(b, x - 8, y - 4, w + 16, h + 18, 30, 0x000000, 0x000000, 80, WHITE, 0); rrect(b, x, y, w, h, 24, 0x161828, 0x10121F, 238, WHITE, 26);
    for i in 0..6 { let on = s.tog >> i & 1 == 1; let tx = x + 16 + (i as i32 % 2) * 160; let ty = y + 16 + (i as i32 / 2) * 72; let hh = hv(s, 70 + i as i32);
        if on { rrect(b, tx, ty, 152, 64, 16, 0x7E22CE, 0x7E22CE, if hh { 130 } else { 100 }, PURPLE, 150); } else { rrect(b, tx, ty, 152, 64, 16, WHITE, WHITE, if hh { 18 } else { 10 }, WHITE, 24); }
        icon(b, TILEI[i], tx + 16, ty + 22, if on { 0xE9D5FF } else { DIM }, 255); text(b, M12, tx + 46, ty + 29, TILES[i], if on { WHITE } else { TXT }, 255); text(b, R12, tx + 46, ty + 47, if on { "On" } else { "Off" }, if on { 0xE9D5FF } else { DIM }, 255); }
    for (k, (cy, v, ic)) in [(264, s.br, I_BRIGHTNESS_6_20), (304, s.vol, I_VOLUME_UP_20)].iter().enumerate() { let _ = k; let v = *v; let cy = *cy; icon(b, *ic, x + 18, y + cy - 10, DIM, 255);
        rrect(b, x + 56, y + cy - 3, 236, 6, 3, WHITE, WHITE, 30, WHITE, 0); let fw = (236 * v / 100).max(6); rrect(b, x + 56, y + cy - 3, fw, 6, 3, 0xA855F7, 0xA855F7, 255, WHITE, 0);
        rrect(b, x + 56 + 236 * v / 100 - 8, y + cy - 8, 16, 16, 8, WHITE, WHITE, 255, WHITE, 0);
        let t = [b'0' + (v / 100) as u8, b'0' + (v / 10 % 10) as u8, b'0' + (v % 10) as u8, b'%']; let t = if v >= 100 { &t[..] } else if v >= 10 { &t[1..] } else { &t[2..] }; text(b, R12, x + 300, y + cy + 4, core::str::from_utf8(t).unwrap_or(""), TXT, 255); }
}
