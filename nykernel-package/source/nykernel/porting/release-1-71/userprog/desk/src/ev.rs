//! Event model of the desktop shell (what the router's messages do to the state). tools/check_desk.py re-implements it independently.
//! Types: 1 POINT(x,y) press, 6 MOVE(x,y), 15 RELEASE(x,y), 16 SUPER, 3 BACK, 5 TEXT(glyph), 2 KEY(1 next / 2 prev / 3 activate), 14 TIME(hour<<8|min).
use crate::scene::*; use crate::nfs;
fn top(s: &St) -> Option<usize> { (0..4).rev().map(|k| s.z[k] as usize).find(|&i| s.ws[i] == 1) }
fn focus(s: &mut St, i: usize) { let mut n = [0u8; 4]; let mut k = 0; for j in 0..4 { if s.z[j] as usize != i { n[k] = s.z[j]; k += 1; } } n[3] = i as u8; s.z = n; }
fn open_app(s: &mut St, i: usize) { s.ws[i] = 1; focus(s, i); }
fn inpanel(ov: u8, id: i32) -> bool { match ov { 1 => (30..=43).contains(&id) || id == 99, 2 => (62..=66).contains(&id), 3 => (70..=78).contains(&id), _ => false } }
fn refresh_hover(s: &mut St) { let id = hit(s, s.mx, s.my); s.hover = if id == 99 || id == 66 || id == 78 || id == -1 || (id >= 100 && id < 140 && (id - 100) % 10 == 0) { -1 } else { id }; }
fn slider(s: &mut St, k: u8, x: i32) { if k == 2 { s.br = qs_val(x); } else { s.vol = qs_val(x); } }
// ---- Files / NyFS glue (the model in tools/check_desk.py mirrors every rule here)
pub fn fs_refresh(s: &mut St) {
    s.ready = unsafe { nfs::READY }; s.nn = 0;
    if !s.ready { s.aun = 0; s.abrk = 0; return; }
    for i in 0..nfs::NAMES { match nfs::list(i) { Some(n) => { let k = s.nn; s.names[k] = n; s.sizes[k] = nfs::size_of(&n[..nfs::nlen(&n)]); s.nn += 1; } None => break } }
    let maxs = (s.nn as i32 - frows(480)).max(0); if s.fscr > maxs && s.fmode == 0 { s.fscr = maxs; }
    audit_check(s);
}
/// the overlay of the game image: the play counter lives in the NyFS file `starlight.sav` (NPS-006 section 4: saves survive; the image itself is read-only)
fn play(s: &mut St) -> i32 {
    if !s.ready { return 0; } let mut b = [0u8; nfs::MAXF]; let (st, sz) = nfs::fread(b"starlight.sav", &mut b); let mut n: i32 = 0;
    if st == 0 { for &c in &b[..sz.min(9)] { if c.is_ascii_digit() { n = n * 10 + (c - b'0') as i32; } } } else if st == -15 { if nfs::create(b"starlight.sav") != 0 { return 0; } } else { return 0; }
    n += 1; let mut o = [0u8; 12]; let l = itoa(n, &mut o); if nfs::fwrite(b"starlight.sav", &o[..l]) != 0 { return 0; } fs_refresh(s); n
}
// ---- tamper-evident audit log (NPS-010 section 8 / ADR-0018, reduced): the NyFS file `auditlog`, one line per event "SEQ|TEXT|HASH" where HASH = first 16 hex digits of
// SHA-256(previous line's HASH ++ "SEQ|TEXT") (genesis: sixteen zeros). Editing or deleting a line breaks the chain from that record on; the System page and `audit` re-verify it every time.
const ZERO16: [u8; 16] = *b"0000000000000000";
fn chain_hash(prev: &[u8], body: &[u8]) -> [u8; 16] {
    let mut buf = [0u8; 16 + 120]; let n = (16 + body.len()).min(136); buf[..16].copy_from_slice(&prev[..16]); buf[16..n].copy_from_slice(&body[..n - 16]);
    let h = crate::sha::sha256(&buf[..n]); let mut o = [0u8; 16]; for k in 0..8 { o[2 * k] = b"0123456789abcdef"[(h[k] >> 4) as usize]; o[2 * k + 1] = b"0123456789abcdef"[(h[k] & 15) as usize]; } o
}
/// walks the log: (number of lines, first bad record number or 0, hash of the last line)
fn audit_walk(d: &[u8]) -> (i32, i32, [u8; 16]) {
    let mut prev = ZERO16; let (mut n, mut bad) = (0i32, 0i32); let mut a = 0;
    for k in 0..=d.len() { if k == d.len() || d[k] == b'\n' { let ln = &d[a..k]; a = k + 1; if ln.is_empty() { continue; } n += 1;
        let mut ok = false; if let Some(p) = ln.iter().rposition(|&c| c == b'|') { let (body, st) = (&ln[..p], &ln[p + 1..]);
            let mut sb = [0u8; 12]; let sl = itoa(n, &mut sb); ok = st.len() == 16 && body.len() <= 120 && body.len() > sl && body[..sl] == sb[..sl] && body[sl] == b'|' && chain_hash(&prev, body)[..] == st[..];
            if st.len() == 16 { prev.copy_from_slice(st); } }
        if !ok && bad == 0 { bad = n; } } }
    (n, bad, prev)
}
pub fn audit_check(s: &mut St) {
    s.aun = 0; s.abrk = 0; if !s.ready { return; } let mut b = [0u8; nfs::MAXF]; let (st, sz) = nfs::fread(b"auditlog", &mut b); if st != 0 { return; }
    let (n, bad, _) = audit_walk(&b[..sz]); s.aun = n; s.abrk = bad;
}
pub fn audit_append(s: &mut St, text: &[u8]) {
    if !s.ready { return; } let mut b = [0u8; nfs::MAXF]; let (st, sz) = nfs::fread(b"auditlog", &mut b);
    if st == -15 { if nfs::create(b"auditlog") != 0 { return; } } else if st != 0 { return; }
    let sz = if st == 0 { sz } else { 0 }; let (n, _, prev) = audit_walk(&b[..sz]);
    let mut body = [0u8; 120]; let mut bl = 0; let mut sb = [0u8; 12]; let sl = itoa(n + 1, &mut sb);
    for &c in sb[..sl].iter().chain(b"|".iter()).chain(text.iter()) { if bl < 120 { body[bl] = if c == b'|' && bl >= sl + 1 { b'/' } else { c }; bl += 1; } }
    let h = chain_hash(&prev, &body[..bl]); let need = bl + 1 + 16 + 1; if sz + need > nfs::MAXF { return; }
    b[sz..sz + bl].copy_from_slice(&body[..bl]); b[sz + bl] = b'|'; b[sz + bl + 1..sz + bl + 17].copy_from_slice(&h); b[sz + bl + 17] = b'\n';
    if nfs::fwrite(b"auditlog", &b[..sz + need]) == 0 { fs_refresh(s); }
}
pub fn audit_boot(s: &mut St) {
    let mode = (s.sysv[0] & 255) as u8; let signer = ((s.sysv[0] >> 8) & 255) as u8;
    let mut t = [0u8; 48]; let m: &[u8] = match mode { 0 => b"boot enforcing ", 1 => b"boot enrollment ", _ => b"boot sb-disabled " }; let g: &[u8] = match signer { 1 => b"platform-key", 2 => b"user-key", _ => b"unsigned" };
    let mut n = 0; for &c in m.iter().chain(g.iter()) { t[n] = c; n += 1; } audit_append(s, &t[..n]);
}
fn audit_run(s: &mut St, bi: usize, res: i32) {
    let mut t = [0u8; 80]; let mut n = 0; let mut sb = [0u8; 12];
    let (name, tail): (&[u8], &[u8]) = (PROGS[bi].0.as_bytes(), if bi >= 8 { b" image " } else { b" C8[EP SHM RPL q2] " });
    let verdict: &[u8] = if bi >= 8 { if res == 0 { b"verified" } else { b"corrupt" } } else if res == 0 { b"loaded" } else if res == -20 { b"refused" } else { b"error" };
    let rl = itoa(if res < 0 { -res } else { res }, &mut sb);
    for &c in b"run ".iter().chain(name.iter()).chain(tail.iter()).chain(verdict.iter()) { if n < 80 { t[n] = c; n += 1; } }
    if res < 0 { for &c in b" -".iter().chain(sb[..rl].iter()) { if n < 80 { t[n] = c; n += 1; } } }
    audit_append(s, &t[..n]);
}
fn open_file(s: &mut St, i: usize) { s.cur = i; let n = s.names[i]; let nm = &n[..nfs::nlen(&n)]; let mut b = [0u8; nfs::MAXF]; let (st, sz) = nfs::fread(nm, &mut b); if st == 0 { s.buf[..sz].copy_from_slice(&b[..sz]); s.blen = sz; } else { s.blen = 0; s.fmsg = 7; } s.dirty = false; s.fscr = 0; s.fmode = 1; if st == 0 { s.fmsg = 0; } }
fn err_msg(st: i64) -> u8 { match st { -9 => 4, -6 => 5, -20 => 6, -19 => 8, _ => 7 } }
fn fs_create(s: &mut St, nm: &[u8]) -> i64 { let st = nfs::create(nm); if st == 0 { fs_refresh(s); } st }
fn find(s: &St, nm: &[u8]) -> Option<usize> { (0..s.nn).find(|&i| &s.names[i][..nfs::nlen(&s.names[i])] == nm) }
fn files_click(s: &mut St, id: i32) {
    match id {
        320 => { s.fmode = 0; s.fscr = 0; s.dirty = false; s.fmsg = 0; fs_refresh(s); }
        321 => { s.fscr = (s.fscr - if s.fmode == 1 { 3 } else { 1 }).max(0); }
        322 => { if s.fmode == 1 { s.fscr = (s.fscr + 3).min((nlines(s) - vrows(s.wh[2])).max(0)); } else { s.fscr = (s.fscr + 1).min((s.nn as i32 - frows(s.wh[2])).max(0)); } }
        323 => { if s.dirty { let n = s.names[s.cur]; let st = nfs::fwrite(&n[..nfs::nlen(&n)], &s.buf[..s.blen]); if st == 0 { s.dirty = false; s.fmsg = 1; fs_refresh(s); } else { s.fmsg = err_msg(st); } } }
        330 => { s.fmode = 2; s.fil = 0; s.fmsg = 0; }
        340..=359 => { let i = (id - 340) as usize; if i < s.nn { s.fmsg = 0; open_file(s, i); } }
        360..=379 => { let i = (id - 360) as usize; if i < s.nn { let n = s.names[i]; let st = nfs::unlink(&n[..nfs::nlen(&n)]); s.fmsg = if st == 0 { 3 } else { err_msg(st) }; fs_refresh(s); } }
        _ => {}
    }
}
fn ps_result(s: &mut St, n: i64) {
    if s.tp == 3 { s.tp = 0; let n = (n.max(0) as usize).min(88); s.pn = 0;
        for i in 0..n { let r = crate::nygi::rec(i); if r[0] == 0 || s.pn >= 16 { continue; } let k = s.pn as usize; s.pl[k][0] = i as u8; s.pl[k][1] = r[1]; s.pl[k][2] = r[2]; s.pl[k][3] = r[3]; for q in 0..8 { s.pl[k][4 + q] = r[8 + q]; } s.pn += 1; }
        return; }
    if s.tp != 1 { return; } s.tp = 0; let n = (n.max(0) as usize).min(88); let mut live = 0;
    for i in 0..n { let r = crate::nygi::rec(i); if r[0] == 0 { continue; } live += 1;
        let mut ib = [0u8; 12]; let il = itoa(i as i32, &mut ib); let mut cb = [0u8; 12]; let cl = itoa(r[1] as i32, &mut cb); let mut pb = [0u8; 12]; let pl = itoa(r[3] as i32, &mut pb);
        let nm: &[u8] = if r[8] == 0 { b"root" } else { let mut e = 8; for k in 0..8 { if r[8 + k] == 0 { e = k; break; } } &r[8..8 + e] };
        let mut l = [0u8; 48]; let mut m = 0; let blk: &[u8] = if r[2] != 0 { b" blk" } else { b"" };
        for part in [&ib[..il], b" ", nm, b" C", &cb[..cl], b" ", &pb[..pl], b"pg", blk] { for &ch in part.iter() { if m < 48 { l[m] = ch; m += 1; } } } tpush(s, &l[..m]); }
    let mut lb = [0u8; 12]; let ll = itoa(live, &mut lb); let mut nb = [0u8; 12]; let nl = itoa(n as i32, &mut nb); let mut l = [0u8; 48]; let mut m = 0;
    for part in [&lb[..ll], b" live of ", &nb[..nl], b" started"] { for &ch in part.iter() { if m < 48 { l[m] = ch; m += 1; } } } tpush(s, &l[..m]);
}
fn kill_result(s: &mut St, n: i64) {
    if s.tp != 2 { return; } s.tp = 0;
    if n < 0 { let mut b = [0u8; 12]; let k = itoa((-n) as i32, &mut b); let (o, m) = tcat(b"kill failed -", &b[..k]); tpush(s, &o[..m]); return; }
    let mut b = [0u8; 12]; let k = itoa(n as i32, &mut b); let (o, m) = tcat(&b[..k], b" loader process(es) killed"); tpush(s, &o[..m]);
}
fn app_match(i: usize, rest: &[u8]) -> bool { let n = APPN[i].as_bytes(); (rest.len() <= n.len() && (0..rest.len()).all(|k| n[k].to_ascii_lowercase() == rest[k])) || (i == 0 && b"hub".starts_with(rest)) }
fn tpush(s: &mut St, t: &[u8]) { if s.thn == 24 { for i in 1..24 { s.th[i - 1] = s.th[i]; s.thl[i - 1] = s.thl[i]; } s.thn = 23; } let n = t.len().min(48); let k = s.thn; s.th[k] = [0; 48]; s.th[k][..n].copy_from_slice(&t[..n]); s.thl[k] = n as u8; s.thn += 1; }
fn tcat(a: &[u8], b: &[u8]) -> ([u8; 48], usize) { let mut o = [0u8; 48]; let mut n = 0; for &c in a.iter().chain(b.iter()) { if n < 48 { o[n] = c; n += 1; } } (o, n) }
/// the terminal's built-in commands (letters and spaces only: that is all the router's keymap delivers)
fn run(s: &mut St) {
    let cmd = s.ti; let cl = s.til; s.til = 0; let c = &cmd[..cl]; let (e, en) = tcat(b"nyrqis@desk:~$ ", c); tpush(s, &e[..en]);
    let sp = c.iter().position(|&x| x == b' ').unwrap_or(cl); let name = &c[..sp]; let rest: &[u8] = if sp < cl { &c[sp + 1..] } else { &[] };
    if name.is_empty() { return; }
    if name == b"help" { tpush(s, b"help uname date whoami apps echo clear ls cat"); tpush(s, b"write rm audit progs run sys"); tpush(s, b"open close layout ps kill spawn"); }
    else if name == b"uname" { tpush(s, b"Nyrqis OS x86_64 (user-space shell)"); }
    else if name == b"date" { let (o, n) = tcat(&s.clock, b" (CMOS clock)"); tpush(s, &o[..n]); }
    else if name == b"whoami" { tpush(s, b"nyrqis"); }
    else if name == b"apps" { for i in 0..4 { let st: &[u8] = match s.ws[i] { 1 => b"open", 2 => b"minimised", _ => b"closed" }; let (o, n) = tcat(APPN[i].as_bytes(), b"  "); let mut l = [0u8; 48]; l[..n].copy_from_slice(&o[..n]); let mut m = n; for &ch in st { if m < 48 { l[m] = ch; m += 1; } } tpush(s, &l[..m]); } }
    else if name == b"echo" { tpush(s, rest); }
    else if name == b"ls" { fs_refresh(s); if !s.ready { tpush(s, b"storage unavailable"); } else if s.nn == 0 { tpush(s, b"(no files)"); } else { for i in 0..s.nn { let n = s.names[i]; let mut zb = [0u8; 12]; let zl = itoa(s.sizes[i], &mut zb); let (o, k) = tcat(&n[..nfs::nlen(&n)], b"  "); let mut l = [0u8; 48]; l[..k].copy_from_slice(&o[..k]); let mut m = k; for &ch in zb[..zl].iter().chain(b" bytes".iter()) { if m < 48 { l[m] = ch; m += 1; } } tpush(s, &l[..m]); } } }
    else if name == b"cat" { let mut b = [0u8; nfs::MAXF]; let (st, sz) = nfs::fread(rest, &mut b); if st == 0 { let mut a = 0; let mut shown = 0; for k in 0..=sz { if k == sz || b[k] == b'\n' { if shown < 6 && !(k == sz && a == k && shown > 0) { let mut l = [0u8; 48]; let mut m = 0; for &ch in &b[a..k] { if m < 48 { l[m] = if (32..127).contains(&ch) { ch } else { b'?' }; m += 1; } } tpush(s, &l[..m]); shown += 1; } a = k + 1; } } } else { let (o, n) = tcat(b"no such file: ", rest); tpush(s, &o[..n]); } }
    else if name == b"write" { let sp2 = rest.iter().position(|&x| x == b' ').unwrap_or(rest.len()); let (fname, text) = (&rest[..sp2], if sp2 < rest.len() { &rest[sp2 + 1..] } else { &[][..] });
        let mut st = if find(s, fname).is_some() { 0 } else { fs_create(s, fname) }; if st == 0 { st = nfs::fwrite(fname, text); fs_refresh(s); }
        if st == 0 { let mut zb = [0u8; 12]; let zl = itoa(text.len() as i32, &mut zb); let (o, n) = tcat(b"wrote ", &zb[..zl]); let mut l = [0u8; 48]; l[..n].copy_from_slice(&o[..n]); let mut m = n; for &ch in b" bytes".iter() { if m < 48 { l[m] = ch; m += 1; } } tpush(s, &l[..m]); } else { tpush(s, FMSG[err_msg(st) as usize].as_bytes()); } }
    else if name == b"rm" { let st = nfs::unlink(rest); if st == 0 { fs_refresh(s); let (o, n) = tcat(b"removed ", rest); tpush(s, &o[..n]); } else { let (o, n) = tcat(b"no such file: ", rest); tpush(s, &o[..n]); } }
    else if name == b"audit" { fs_refresh(s); let mut b = [0u8; nfs::MAXF]; let (st, sz) = nfs::fread(b"auditlog", &mut b);
        if !s.ready { tpush(s, b"storage unavailable"); } else if st != 0 { tpush(s, b"no audit log yet"); } else { let (n, bad, _) = audit_walk(&b[..sz]); let mut nb = [0u8; 12]; let nl = itoa(n, &mut nb);
            if bad == 0 { let (o, k) = tcat(&nb[..nl], b" events, hash chain intact"); tpush(s, &o[..k]); } else { let mut kb = [0u8; 12]; let kl = itoa(bad, &mut kb); let (o, k) = tcat(&nb[..nl], b" events, BROKEN at record "); let mut l = [0u8; 48]; l[..k].copy_from_slice(&o[..k]); let mut m = k; for &ch in &kb[..kl] { if m < 48 { l[m] = ch; m += 1; } } tpush(s, &l[..m]); }
            let mut lines = [(0usize, 0usize); 3]; let mut cnt = 0; let mut a = 0; for k in 0..=sz { if k == sz || b[k] == b'\n' { if k > a { lines[cnt % 3] = (a, k); cnt += 1; } a = k + 1; } }
            let first = if cnt > 3 { cnt - 3 } else { 0 }; for j in first..cnt { let (x, y) = lines[j % 3]; let mut l = [0u8; 48]; let mut m = 0; for &ch in &b[x..y] { if m < 48 { l[m] = if (32..127).contains(&ch) { ch } else { b'?' }; m += 1; } } tpush(s, &l[..m]); } } }
    else if name == b"progs" { for i in 0..NP { let (o, n) = tcat(PROGS[i].0.as_bytes(), b"  "); let mut l = [0u8; 48]; l[..n].copy_from_slice(&o[..n]); let mut m = n; for &ch in PROGS[i].1.as_bytes() { if m < 48 { l[m] = ch; m += 1; } } tpush(s, &l[..m]); } }
    else if name == b"run" { match (0..NP).find(|&i| !rest.is_empty() && PROGS[i].0.as_bytes().starts_with(rest)) {
        None => { let (o, n) = tcat(b"no such program: ", rest); tpush(s, &o[..n]); }
        Some(i) => if s.arun >= 0 || s.tp != 0 { tpush(s, b"launcher busy"); } else { s.arun = i as i8; s.want = i as i16; s.ares[i] = NONE; s.tr = i as i8; let (o, n) = tcat(b"launching ", PROGS[i].0.as_bytes()); tpush(s, &o[..n]); } } }
    else if name == b"sys" { let (mode, signer) = ((s.sysv[0] & 255) as u8, ((s.sysv[0] >> 8) & 255) as u8);
        if !s.sysok { tpush(s, b"waiting for the kernel..."); } else {
            let m: &[u8] = match mode { 0 => b"enforcing", 1 => b"enrollment", _ => b"disabled" }; let g: &[u8] = match signer { 1 => b"platform key", 2 => b"user key", _ => b"unsigned" };
            let (o, n) = tcat(b"secure boot: ", m); let mut l = [0u8; 48]; l[..n].copy_from_slice(&o[..n]); let mut k = n; for &ch in b", ".iter().chain(g.iter()) { if k < 48 { l[k] = ch; k += 1; } } tpush(s, &l[..k]);
            let mut hx = [0u8; 16]; for q in 0..16 { hx[q] = b"0123456789abcdef"[((s.sysv[1] >> (60 - 4 * q)) & 15) as usize]; }
            let mut nb = [0u8; 12]; let nl = itoa(s.sysv[2] as i32, &mut nb); let (o, n) = tcat(b"image: ", &nb[..nl]); let mut l = [0u8; 48]; l[..n].copy_from_slice(&o[..n]); let mut k = n; for &ch in b" bytes ".iter().chain(hx.iter()) { if k < 48 { l[k] = ch; k += 1; } } tpush(s, &l[..k]);
            let mut sb = [0u8; 48]; let t = kp_text(s.sysv[0], &mut sb); let (o, n) = tcat(t.as_bytes(), b""); tpush(s, &o[..n]); } }
    else if name == b"open" || name == b"close" { match (0..4).find(|&i| !rest.is_empty() && app_match(i, rest)) {
        None => { let (o, n) = tcat(b"no such app: ", rest); tpush(s, &o[..n]); }
        Some(i) => { if name == b"open" { open_app(s, i); } else { s.ws[i] = 0; } let (o, n) = tcat(if name == b"open" { b"opened " } else { b"closed " }, APPN[i].as_bytes()); tpush(s, &o[..n]); } } }
    else if name == b"layout" { s.layout = (s.layout + 1) % 3; tpush(s, match s.layout { 0 => b"layout: floating", 1 => b"layout: columns", _ => b"layout: grid" }); }
    else if name == b"ps" { if s.arun >= 0 || s.tp != 0 { tpush(s, b"launcher busy"); } else { s.want = 100; s.tp = 1; } }
    else if name == b"kill" { if rest != b"loaders" { tpush(s, b"usage: kill loaders"); } else if s.arun >= 0 || s.tp != 0 { tpush(s, b"launcher busy"); } else { s.want = 101; s.tp = 2; } }
    else if name == b"spawn" { if rest != b"spin" { tpush(s, b"usage: spawn spin"); } else if s.arun >= 0 || s.tp != 0 { tpush(s, b"launcher busy"); } else { s.want = 10; s.tp = 5; } }
    else if name == b"clear" { s.thn = 0; }
    else { let (o, n) = tcat(b"unknown command: ", name); tpush(s, &o[..n]); }
}
pub fn event(s: &mut St, t: u64, a: i64, b: i64) -> (bool, u64) {
    let mut reply = 0u64;
    match t {
        100 => { if b == 102 { if s.tp == 4 { s.tp = 3; s.want = 100; } return (true, 0); }
            if b == 10 { if s.tp == 5 { s.tp = 0; if a == 0 { tpush(s, b"spin started"); } else { let mut nb = [0u8; 12]; let nl = itoa(if a < 0 { (-a) as i32 } else { a as i32 }, &mut nb); let (o, n) = tcat(b"spin failed, code -", &nb[..nl]); tpush(s, &o[..n]); } } return (true, 0); }
            if b == 100 { ps_result(s, a); return (true, 0); } if b == 101 { kill_result(s, a); return (true, 0); } if (0..10).contains(&b) { let bi = b as usize; let mut res = a as i32;
            if bi >= 8 { if a > 0 { let (code, nf) = crate::nygi::verify(a as usize); res = code; s.anf[bi] = nf; if code == 0 { s.apl[bi] = play(s); } } else if a == 0 { res = -1; } }   // a game image: livesh sent its size, the desktop verifies the bytes itself
            if bi < 8 && res == 0 && s.pout >> 63 != 0 { s.aret[bi] = s.pout as u32 as i32; s.aok |= 1 << bi; } else { s.aok &= !(1u16 << bi); }
            s.ares[bi] = res; audit_run(s, bi, res); if s.arun == b as i8 { s.arun = -1; }
            if s.tr == b as i8 { s.tr = -1; let mut nb = [0u8; 12]; let nl = itoa(if res < 0 { -res } else { res }, &mut nb); let mut fb = [0u8; 12]; let fl = itoa(s.anf[bi], &mut fb); let mut rb = [0u8; 12]; let rl = itoa(s.aret[bi].wrapping_abs(), &mut rb);
                let (o, n) = tcat(PROGS[bi].0.as_bytes(), b": "); let mut l = [0u8; 48]; l[..n].copy_from_slice(&o[..n]); let mut k = n;
                let parts: [&[u8]; 4] = if bi >= 8 { if res == 0 { [b"verified, ", &fb[..fl], b" files", b""] } else { [b"corrupt (-", &nb[..nl], b")", b""] } } else if res == 0 { if s.aok >> bi & 1 != 0 { [if bi < 4 { &b"loaded, exit code "[..] } else { &b"loaded, returned "[..] }, if s.aret[bi] < 0 { b"-" } else { b"" }, &rb[..rl], b""] } else { [b"loaded", b"", b"", b""] } } else if res == -20 { [b"refused (-20)", b"", b"", b""] } else { [b"error ", &nb[..nl], b"", b""] };
                for pt in parts.iter() { for &ch in pt.iter() { if k < 48 { l[k] = ch; k += 1; } } } tpush(s, &l[..k]); }
            return (true, 0); } return (false, 0); }
        1 => { s.mx = (a as i32).clamp(0, 1023); s.my = (b as i32).clamp(0, 767); s.down = true; let id = hit(s, s.mx, s.my); let was = s.ov; reply = if id == -1 { 0 } else { id as u64 + 0x100 };
            if !inpanel(was, id) && id != 60 && id != 61 && id != 1 { s.ov = 0; }
            match id {
                1 => { s.ov = if was == 1 { 0 } else { 1 }; s.qn = 0; } 60 => s.ov = if was == 2 { 0 } else { 2 }, 61 => s.ov = if was == 3 { 0 } else { 3 }, 6 => s.layout = (s.layout + 1) % 3,
                10..=13 => { let i = (id - 10) as usize; if s.ws[i] == 1 && top(s) == Some(i) { s.ws[i] = 2; } else { open_app(s, i); } }
                50..=53 => open_app(s, (id - 50) as usize), 30..=33 => { open_app(s, (id - 30) as usize); s.ov = 0; }
                62..=64 => s.dism |= 1 << (id - 62), 65 => s.dism = 7, 70..=75 => s.tog ^= 1 << (id - 70),
                76 | 77 => { let k = if id == 76 { 2 } else { 3 }; s.drag = (k, 0, 0); slider(s, k, s.mx); }
                100..=139 => { let i = ((id - 100) / 10) as usize; match (id - 100) % 10 {
                    0 => focus(s, i), 1 => s.ws[i] = 0, 2 => s.ws[i] = 2, 3 => s.layout = (s.layout + 1) % 3,
                    5 => { focus(s, i); if s.layout == 0 { s.drag = (4, s.ww[i] - s.mx, s.wh[i] - s.my); } }
                    _ => { focus(s, i); if s.layout == 0 { s.drag = (1, s.mx - s.wx[i], s.my - s.wy[i]); } } } }
                320..=379 => { files_click(s, id); }
                400..=409 => { let a = (id - 400) as usize; if s.arun < 0 { s.arun = a as i8; s.want = a as i16; s.ares[a] = NONE; } }
                430 => { s.hmode = 0; } 431 | 432 => { s.hmode = 1; if s.arun < 0 && s.tp == 0 { s.want = 100; s.tp = 3; } }
                440..=455 => { let k = (id - 440) as usize; if k < s.pn as usize && s.arun < 0 && s.tp == 0 { s.want = 200 + s.pl[k][0] as i16; s.tp = 4; } }
                410 => { s.ascr = (s.ascr - 1).max(0); } 411 => { s.ascr = (s.ascr + 1).min((10 - frows(s.wh[0]).min(10)).max(0)); }
                _ => {} }
            refresh_hover(s); return (true, reply); }
        6 => { let (x, y) = ((a as i32).clamp(0, 1023), (b as i32).clamp(0, 767)); if (x, y) == (s.mx, s.my) { return (false, 0); } s.mx = x; s.my = y;
            if s.down && s.drag.0 == 1 { let i = s.z[3] as usize; s.wx[i] = (x - s.drag.1).clamp(-(s.ww[i] - 120), 1024 - 120); s.wy[i] = (y - s.drag.2).clamp(41, 700); s.hover = -1; return (true, 0); }
            if s.down && s.drag.0 == 4 { let i = s.z[3] as usize; s.ww[i] = (x + s.drag.1).clamp(360, (1024 - s.wx[i]).max(360)); s.wh[i] = (y + s.drag.2).clamp(260, (768 - s.wy[i]).max(260)); s.hover = -1; return (true, 0); }
            if s.down && s.drag.0 >= 2 { let k = s.drag.0; slider(s, k, x); s.hover = -1; return (true, 0); }
            let old = s.hover; refresh_hover(s); return (old != s.hover, 0); }
        15 => { s.down = false; s.drag = (0, 0, 0); s.mx = (a as i32).clamp(0, 1023); s.my = (b as i32).clamp(0, 767); refresh_hover(s); return (true, 0); }
        16 => { s.ov = if s.ov == 1 { 0 } else { 1 }; s.qn = 0; refresh_hover(s); return (true, 0); }
        3 => { if s.ov == 0 && top(s) == Some(2) && s.fmode != 0 { files_click(s, 320); refresh_hover(s); return (true, 0); } s.ov = 0; refresh_hover(s); return (true, 0); }
        5 => { if s.ov == 1 { let a = a as u32; let c = if a < 26 { b'a' + a as u8 } else if (32..58).contains(&a) { b'a' + (a - 32) as u8 } else if a == 26 { b' ' } else { 0 };
            if c != 0 && s.qn < 24 { s.q[s.qn] = c; s.qn += 1; } else if a == 255 && s.qn > 0 { s.qn -= 1; } refresh_hover(s); return (true, 0); }
            if s.ov == 0 && top(s) == Some(2) && s.fmode != 0 { let a = a as u32;
                if s.fmode == 2 { let c = if a < 26 { b'a' + a as u8 } else if (32..58).contains(&a) { b'a' + (a - 32) as u8 } else { 0 };
                    if c != 0 && s.fil < 15 { s.fin[s.fil] = c; s.fil += 1; return (true, 0); } if a == 255 && s.fil > 0 { s.fil -= 1; return (true, 0); } return (false, 0); }
                let c = if a < 26 { b'A' + a as u8 } else if (32..58).contains(&a) { b'a' + (a - 32) as u8 } else if a == 26 { b' ' } else { 0 };
                if c != 0 && s.blen < nfs::MAXF { s.buf[s.blen] = c; s.blen += 1; s.dirty = true; s.fmsg = 0; return (true, 0); } if c != 0 { s.fmsg = 8; return (true, 0); }
                if a == 255 && s.blen > 0 { s.blen -= 1; s.dirty = true; s.fmsg = 0; return (true, 0); } return (false, 0); }
            if s.ov == 0 && top(s) == Some(3) { let a = a as u32; let c = if a < 26 { b'A' + a as u8 } else if (32..58).contains(&a) { b'a' + (a - 32) as u8 } else if a == 26 { b' ' } else { 0 };
                if c != 0 && s.til < 30 { s.ti[s.til] = c; s.til += 1; return (true, 0); } if a == 255 && s.til > 0 { s.til -= 1; return (true, 0); } } return (false, 0); }
        2 => { if (a == 1 || a == 2) && s.ov == 0 {          // Tab / Shift+Tab: focus the next / previous visible window
                let mut v = [0usize; 4]; let mut n = 0; for k in 0..4 { let i = s.z[k] as usize; if s.ws[i] == 1 { v[n] = i; n += 1; } }
                if n < 2 { return (false, 0); }
                if a == 1 { focus(s, v[0]); } else { let t = v[n - 1]; let mut nz = [t as u8; 4]; let mut k = 1; for j in 0..4 { if s.z[j] as usize != t { nz[k] = s.z[j]; k += 1; } } s.z = nz; }
                refresh_hover(s); return (true, 0); }
            if a == 3 && s.ov == 0 && top(s) == Some(2) && s.fmode == 2 { let nm = s.fin; let l = s.fil; if l == 0 { s.fmsg = 6; s.fmode = 0; return (true, 0); }
                let st = fs_create(s, &nm[..l]); if st == 0 { let i = find(s, &nm[..l]); match i { Some(i) => open_file(s, i), None => { s.fmode = 0; s.fmsg = 7; } } } else { s.fmode = 0; s.fmsg = err_msg(st); } return (true, 0); }
            if a == 3 && s.ov == 0 && top(s) == Some(2) && s.fmode == 1 && s.blen < nfs::MAXF { s.buf[s.blen] = b'\n'; s.blen += 1; s.dirty = true; s.fmsg = 0; return (true, 0); }
            if a == 3 && s.ov == 0 && top(s) == Some(3) { run(s); return (true, 0); } if a == 3 && s.ov == 1 { let (m, n) = matches(s); if n > 0 { open_app(s, m[0] as usize); s.ov = 0; refresh_hover(s); return (true, 0); } } return (false, 0); }
        14 => { let (hh, mm) = ((a >> 8) as u8, (a & 255) as u8); if hh < 24 && mm < 60 { s.clock = [b'0' + hh / 10, b'0' + hh % 10, b':', b'0' + mm / 10, b'0' + mm % 10]; return (true, 0); } return (false, 0); }
        _ => {} }
    (false, reply)
}
