#!/usr/bin/env python3
"""Independent model of the desktop shell `desk` (increments 1-54/1-55): a numpy re-implementation of the integer renderer, the scene, the hit-testing and the event model,
written from the spec (not generated from the Rust). tools/live_desk_test.py compares the REAL emulated screen with `frame(state)` PIXEL FOR PIXEL, including the cursor sprite.
Shared with the Rust code only: the DATA tables (userprog/desk/src/{font.bin,icons.bin,assets.rs} from tools/make_desk_assets.py). `python3 tools/check_desk.py [--png out.png]` runs self-checks."""
import os, re, struct, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); SRC = os.path.join(HERE, '..', 'userprog', 'desk', 'src')
W, H = 1024, 768
FONT = open(os.path.join(SRC, 'font.bin'), 'rb').read(); ICONS = open(os.path.join(SRC, 'icons.bin'), 'rb').read(); AS = open(os.path.join(SRC, 'assets.rs')).read()
NF = int(re.search(r'NFACES: usize = (\d+)', AS).group(1)); MASK_BASE = int(re.search(r'MASK_BASE: usize = (\d+)', AS).group(1))
ICON_TAB = [tuple(map(int, m)) for m in re.findall(r'\((\d+), (\d+)\)', AS.split('ICON_TAB')[1].split('= [')[1].split(';')[0])]
CONST = {k: int(v) for k, v in re.findall(r'pub const ([A-Z0-9_]+): usize = (\d+);', AS)}
G = {n: CONST[n] for n in CONST if re.fullmatch(r'[RM]\d+', n)}; I = {n: CONST[n] for n in CONST if n.startswith('I_')}
GL = np.frombuffer(FONT, dtype='<i4', count=(2 * NF + NF * 95 * 6))[2 * NF:].reshape(NF, 95, 6)
MASKS = np.frombuffer(FONT, dtype=np.uint8, offset=MASK_BASE); ICM = np.frombuffer(ICONS, dtype=np.uint8)
WHITE, TXT, DIM, PURPLE, BLUE, AMBER, TEAL = 0xFFFFFF, 0xE2E8F0, 0x94A3B8, 0xC084FC, 0x38BDF8, 0xFBBF24, 0x2DD4BF
APPC = [PURPLE, BLUE, AMBER, TEAL]; APPN = ["Console Hub", "System", "Files", "Terminal"]
APPI28 = [I['I_SPORTS_ESPORTS_28'], I['I_SETTINGS_28'], I['I_FOLDER_28'], I['I_TERMINAL_28']]; APPI24 = [I['I_SPORTS_ESPORTS_24'], I['I_SETTINGS_24'], I['I_FOLDER_24'], I['I_TERMINAL_24']]
DOCK = (337, 700, 350, 58); DOCK_X = [376, 443, 493, 540, 590]; DOCK_VIEW = 652; START = (242, 372, 540, 316); NOTIF = (16, 48, 380, 324); QS = (664, 48, 344, 340)
DESK_Y = [72, 194, 300, 406]; TA = (16, 52, 892, 636); FW, FH = 520, 430
NOTIFS = [("Update ready", "Nyrqis 1-55 can be installed", "now", PURPLE), ("Console Hub", "Cloud Stream session ended", "5m", 0x34D399), ("Files", "4 items synced", "1h", AMBER)]
TILES = ["Wi-Fi", "Bluetooth", "Controller", "Do not disturb", "Night light", "Airplane"]
TILEI = [I[k] for k in ('I_WIFI_20', 'I_BLUETOOTH_20', 'I_SPORTS_ESPORTS_20', 'I_DO_NOT_DISTURB_ON_20', 'I_NIGHTLIGHT_20', 'I_AIRPLANEMODE_ACTIVE_20')]
# ---------------------------------------------------------------- renderer
class Canvas:
    def __init__(s): s.a = np.zeros((H, W, 3), dtype=np.int64); s.clip = (0, 0, W, H)
    def setclip(s, x, y, w, h): s.clip = (max(x, 0), max(y, 0), min(x + w, W), min(y + h, H))
    def clearclip(s): s.clip = (0, 0, W, H)
    def blend(s, y0, y1, x0, x1, rgb, a):            # a: scalar or (h, w) int array in 0..255
        if y1 <= y0 or x1 <= x0: return
        reg = s.a[y0:y1, x0:x1]; a = np.broadcast_to(np.asarray(a, dtype=np.int64), reg.shape[:2])[..., None]
        src = np.array([(rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255], dtype=np.int64) if np.isscalar(rgb) else rgb
        out = (reg * (255 - a) + src * a + 127) // 255
        s.a[y0:y1, x0:x1] = np.where(a == 0, reg, out)
    def rect(s, x, y, w, h, rgb, a): c = s.clip; s.blend(max(y, c[1]), min(y + h, c[3]), max(x, c[0]), min(x + w, c[2]), rgb, a)
    def rrect(s, x, y, w, h, r, f0, f1, fa, brgb, ba):
        c = s.clip; y0, y1, x0, x1 = max(y, c[1]), min(y + h, c[3]), max(x, c[0]), min(x + w, c[2])
        if y1 <= y0 or x1 <= x0: return
        cx, cy, hx, hy, r16 = (x * 2 + w) * 8, (y * 2 + h) * 8, w * 8, h * 8, r * 16
        py = np.arange(y0, y1)[:, None]; px = np.arange(x0, x1)[None, :]
        co = cover(px, py, cx, cy, hx, hy, r16); ci = cover(px, py, cx, cy, hx - 16, hy - 16, max(r16 - 16, 0)) if ba != 0 else co
        if f0 == f1: col = f0
        else:
            t = (py - y); n = np.arange(3); sh = 16 - 8 * n
            a0 = (f0 >> sh) & 255; a1 = (f1 >> sh) & 255; col = (a0[None, :] * (h - t)[:, :, None] * 0 + (a0 * (h - t)[:, :, None] + a1 * t[:, :, None] + h // 2) // h) if False else ((a0 * (h - t)[:, :, None] + a1 * t[:, :, None] + h // 2) // h)
            col = np.broadcast_to(col, (y1 - y0, x1 - x0, 3)) if col.shape[1] != x1 - x0 else col
        s.blend(y0, y1, x0, x1, col, fa * ci // 255)
        if ba != 0: s.blend(y0, y1, x0, x1, brgb, ba * np.maximum(co - ci, 0) // 255)
    def text(s, face, x, base, st, rgb, a):
        pen = x; c = s.clip
        for ch in st.encode():
            if not 32 <= ch < 127: continue
            off, gw, gh, bx, top, adv = (int(v) for v in GL[face, ch - 32])
            if gw and gh:
                m = MASKS[off:off + gw * gh].reshape(gh, gw).astype(np.int64); gx0, gy0 = pen + bx, base + top
                y0, y1, x0, x1 = max(gy0, c[1]), min(gy0 + gh, c[3]), max(gx0, c[0]), min(gx0 + gw, c[2])
                if y1 > y0 and x1 > x0: s.blend(y0, y1, x0, x1, rgb, m[y0 - gy0:y1 - gy0, x0 - gx0:x1 - gx0] * a // 255)
            pen += adv
        return pen - x
    def icon(s, idx, x, y, rgb, a):
        off, sz = ICON_TAB[idx]; m = ICM[off:off + sz * sz].reshape(sz, sz).astype(np.int64); c = s.clip
        y0, y1, x0, x1 = max(y, c[1]), min(y + sz, c[3]), max(x, c[0]), min(x + sz, c[2])
        if y1 > y0 and x1 > x0: s.blend(y0, y1, x0, x1, rgb, m[y0 - y:y1 - y, x0 - x:x1 - x] * a // 255)
def isqrt(n):
    f = np.floor(np.sqrt(n.astype(np.float64))).astype(np.int64); f = np.where(f * f > n, f - 1, f); f = np.where((f + 1) * (f + 1) <= n, f + 1, f); return f
def cover(px, py, cx, cy, hx, hy, r):
    dx = np.abs(px * 16 + 8 - cx); dy = np.abs(py * 16 + 8 - cy); a = dx - hx + r; b = dy - hy + r; ma = np.maximum(a, 0); mb = np.maximum(b, 0)
    l = isqrt(ma * ma + mb * mb); sd = np.minimum(np.maximum(a, b), 0) + l - r
    return (np.clip(8 - sd, 0, 16) * 255) // 16
def text_w(face, st): return sum(int(GL[face, ch - 32][5]) for ch in st.encode() if 32 <= ch < 127)
def wallpaper():
    ys, xs = np.mgrid[0:H, 0:W].astype(np.int64); c = np.stack([np.full((H, W), v, dtype=np.int64) for v in (7, 9, 22)], -1)
    for gx, gy, rr, col, amp in ((830, 650, 560, (96, 40, 150), 200), (120, 60, 430, (30, 60, 150), 150), (540, 380, 700, (40, 20, 80), 70)):
        d2 = (xs - gx) ** 2 + (ys - gy) ** 2; r2 = rr * rr; m = d2 < r2; t = np.where(m, (r2 - d2) * 255 // r2, 0); t = t * t // 255 * amp // 255
        for k in range(3): c[..., k] = np.where(m, (c[..., k] * (255 - t) + col[k] * t + 127) // 255, c[..., k])
    return c
WALL = None
# ---------------------------------------------------------------- state, geometry, hit-testing, events
FMSG = ["", "Saved", "Created", "Deleted", "A file with that name exists", "Directory full (20 files)", "Bad name", "Storage error", "Too large (2016 bytes max)"]
class NyFS:
    """the NyFS service as the desktop sees it: 20 directory slots (LIST = i-th occupied slot), names 1..15 bytes, files <= 2016 bytes"""
    def __init__(s): s.slots = [None] * 20
    def names(s): return [e for e in s.slots if e is not None]
    def find(s, n):
        for e in s.slots:
            if e is not None and e[0] == n: return e
        return None
    def create(s, n):
        if len(n) == 0 or len(n) > 15: return -20
        if s.find(n): return -9
        for i in range(20):
            if s.slots[i] is None: s.slots[i] = [n, b""]; return 0
        return -6
    def fwrite(s, n, d):
        if len(d) > 2016: return -19
        e = s.find(n)
        if e is None: return -15
        e[1] = bytes(d); return 0
    def fread(s, n):
        e = s.find(n)
        return (-15, b"") if e is None else (0, e[1])
    def unlink(s, n):
        for i in range(20):
            if s.slots[i] is not None and s.slots[i][0] == n: s.slots[i] = None; return 0
        return -15
class St:
    def __init__(s, ready=True):
        s.ov = 0; s.ws = [1, 0, 0, 0]; s.wx = [70, 106, 142, 178]; s.wy = [78, 108, 138, 168]; s.ww = [FW] * 4; s.wh = [FH] * 4; s.z = [0, 1, 2, 3]; s.layout = 0; s.clock = "--:--"; s.mx = 512; s.my = 384; s.hover = -1
        s.down = False; s.drag = (0, 0, 0); s.dism = 0; s.tog = 7; s.br = 80; s.vol = 60; s.q = ""; s.th = []; s.ti = ""
        s.fmode = 0; s.fscr = 0; s.fin = ""; s.cur = 0; s.buf = b""; s.dirty = False; s.fmsg = 0; s.ready = ready; s.names = []; s.sizes = []; s.nyfs = NyFS(); s.sysok = False; s.sysv = (0, 0, 0); s.tr = -1; s.tp = 0; s.hmode = 0; s.pn = 0; s.pl = []; s.psdata = []; s.pout = 0; s.aret = [0] * 10; s.aok = 0; s.aun = 0; s.abrk = 0; s.ares = [None] * 10; s.anf = [0] * 10; s.apl = [0] * 10; s.arun = -1; s.want = -1; s.ascr = 0
def inside(x, y, r): return r[0] <= x < r[0] + r[2] and r[1] <= y < r[1] + r[3]
def within(x, y, cx, cy, h): return abs(x - cx) <= h and abs(y - cy) <= h
def nopen(s): return sum(1 for v in s.ws if v == 1)
def wrect(s, i):
    if s.layout == 0: return (s.wx[i], s.wy[i], s.ww[i], s.wh[i])
    n = nopen(s); k = sum(1 for j in range(i) if s.ws[j] == 1)
    if s.layout == 1: cw = (TA[2] - 12 * (n - 1)) // n; return (TA[0] + k * (cw + 12), TA[1], cw, TA[3])
    cols = 1 if n == 1 else 2; rows = (n + cols - 1) // cols; ch = (TA[3] - 12 * (rows - 1)) // rows; cw = (TA[2] - 12 * (cols - 1)) // cols; r, c = k // cols, k % cols
    span = n > 1 and n % 2 == 1 and k == n - 1; return (TA[0] + c * (cw + 12), TA[1] + r * (ch + 12), TA[2] if span else cw, ch)
def matches(s): return [i for i in range(4) if s.q == "" or s.q in APPN[i].lower()]
def qs_val(x): return min(max(int((x - (QS[0] + 56)) * 100 / 236), 0), 100)         # int() truncates toward zero like Rust's `/`
def notif_slot(s, i): return None if s.dism >> i & 1 else sum(1 for j in range(i) if not s.dism >> j & 1)
def ncount(s): return 3 - bin(s.dism & 7).count('1')
def frows(h): return max((h - 98 - 12) // 38, 1)
def vrows(h): return max((h - 96 - 14 - 14) // 20, 1)
def nlines(s): return 1 + s.buf.count(b"\n")
def vline(s, i):
    ls = s.buf.split(b"\n"); return "".join(chr(c) if 32 <= c < 127 else "?" for c in ls[i][:110]) if i < len(ls) else ""
PROGS = [("hello.exe", "Windows PE"), ("pewin.exe", "Windows PE"), ("pereloc.exe", "Windows PE"), ("bad_mz.exe", "Windows PE"), ("hello.dex", "Android DEX"), ("ops.dex", "Android DEX"), ("calls.dex", "Android DEX"), ("dex_magic.dex", "Android DEX"), ("demo.nygi", "Game image"), ("bad.nygi", "Game image")]
def killable(r): return bytes(r[4:]).startswith(b"peload") or bytes(r[4:]).startswith(b"dexload")
def ahit(s, r, x, y):
    if y < r[1] + 41: return -1
    if inside(x, y, (r[0] + 24, r[1] + 52, 92, 30)): return 430
    if inside(x, y, (r[0] + 124, r[1] + 52, 100, 30)): return 431
    if s.hmode == 1:
        if inside(x, y, (r[0] + r[2] - 100, r[1] + 52, 76, 30)): return 432
        for k in range(min(frows(r[3]), 16)):
            if k >= s.pn: break
            if killable(s.pl[k]) and inside(x, y, (r[0] + r[2] - 24 - 12 - 62, r[1] + 98 + 38 * k + 5, 62, 24)): return 440 + k
        return -1
    nr = min(frows(r[3]), 10)
    if nr < 10:
        if within(x, y, r[0] + r[2] - 60, r[1] + 67, 14): return 410
        if within(x, y, r[0] + r[2] - 28, r[1] + 67, 14): return 411
    for k in range(nr):
        a = s.ascr + k
        if a >= 10: break
        if inside(x, y, (r[0] + 24, r[1] + 98 + 38 * k, r[2] - 48, 34)): return 400 + a
    return -1
def fhit(s, r, x, y):
    if y < r[1] + 41 or not s.ready: return -1
    if s.fmode == 0:
        if inside(x, y, (r[0] + 24, r[1] + 52, 112, 30)): return 330
        nr = frows(r[3])
        if len(s.names) > nr:
            if within(x, y, r[0] + r[2] - 60, r[1] + 67, 14): return 321
            if within(x, y, r[0] + r[2] - 28, r[1] + 67, 14): return 322
        for k in range(nr):
            a = s.fscr + k
            if a >= len(s.names): break
            ry = r[1] + 98 + 38 * k
            if within(x, y, r[0] + r[2] - 44, ry + 17, 14): return 360 + a
            if inside(x, y, (r[0] + 24, ry, r[2] - 48, 34)): return 340 + a
        return -1
    if s.fmode == 1:
        if inside(x, y, (r[0] + 24, r[1] + 52, 76, 30)): return 320
        if inside(x, y, (r[0] + r[2] - 100, r[1] + 52, 76, 30)): return 323
        if within(x, y, r[0] + r[2] - 40, r[1] + 110, 14): return 321
        if within(x, y, r[0] + r[2] - 40, r[1] + r[3] - 28, 14): return 322
    return -1
def hit(s, x, y):
    if s.ov == 1:
        if inside(x, y, START):
            m = matches(s)
            for j, a in enumerate(m):
                if within(x, y, START[0] + 71 + 126 * j, START[1] + 150, 36): return 30 + a
            if inside(x, y, (START[0] + 24, START[1] + 24, 492, 40)): return 42
            if x >= START[0] + 540 - 110 and START[1] + 86 <= y < START[1] + 108 and x < START[0] + 540 - 20: return 43
            if within(x, y, START[0] + 540 - 80, START[1] + 316 - 38, 18): return 41
            if within(x, y, START[0] + 540 - 38, START[1] + 316 - 38, 18): return 40
            return 99
    elif s.ov == 2:
        if inside(x, y, NOTIF):
            for i in range(3):
                sl = notif_slot(s, i)
                if sl is not None and within(x, y, NOTIF[0] + 12 + 356 - 22, NOTIF[1] + 56 + sl * 72 + 32, 14): return 62 + i
            if inside(x, y, (NOTIF[0] + 380 - 100, NOTIF[1] + 10, 90, 30)): return 65
            return 66
    elif s.ov == 3:
        if inside(x, y, QS):
            for i in range(6):
                if inside(x, y, (QS[0] + 16 + (i % 2) * 160, QS[1] + 16 + (i // 2) * 72, 152, 64)): return 70 + i
            if inside(x, y, (QS[0] + 48, QS[1] + 264 - 14, 252, 28)): return 76
            if inside(x, y, (QS[0] + 48, QS[1] + 304 - 14, 252, 28)): return 77
            return 78
    if inside(x, y, (16, 4, 170, 32)): return 60
    if inside(x, y, (786, 4, 224, 32)): return 61
    if inside(x, y, DOCK):
        if within(x, y, DOCK_X[0], 729, 22): return 1
        for i in range(4):
            if within(x, y, DOCK_X[i + 1], 729, 22): return 10 + i
        if within(x, y, DOCK_VIEW, 729, 22): return 6
        return 99
    for k in (3, 2, 1, 0):
        i = s.z[k]
        if s.ws[i] != 1: continue
        r = wrect(s, i)
        if not inside(x, y, r): continue
        b = 100 + 10 * i
        if within(x, y, r[0] + r[2] - 28, r[1] + 20, 12): return b + 1
        if within(x, y, r[0] + r[2] - 62, r[1] + 20, 12): return b + 2
        if within(x, y, r[0] + r[2] - 89, r[1] + 20, 12): return b + 3
        if s.layout == 0 and x >= r[0] + r[2] - 18 and y >= r[1] + r[3] - 18: return b + 5
        if i == 0:
            f = ahit(s, r, x, y)
            if f != -1: return f
        if i == 2:
            f = fhit(s, r, x, y)
            if f != -1: return f
        return b + 4 if y < r[1] + 40 else b
    for i in range(4):
        if inside(x, y, (924, DESK_Y[i], 56, 56)): return 50 + i
    return -1
def top(s):
    for k in (3, 2, 1, 0):
        if s.ws[s.z[k]] == 1: return s.z[k]
    return None
def focus(s, i): s.z = [j for j in s.z if j != i] + [i]
def open_app(s, i): s.ws[i] = 1; focus(s, i)
def inpanel(ov, i): return {1: 30 <= i <= 43 or i == 99, 2: 62 <= i <= 66, 3: 70 <= i <= 78}.get(ov, False)
def refresh_hover(s):
    i = hit(s, s.mx, s.my); s.hover = -1 if i in (99, 66, 78, -1) or (100 <= i < 140 and (i - 100) % 10 == 0) else i
def clamp(v, lo, hi): return max(lo, min(hi, v))
def fs_refresh(s):
    if not s.ready: s.names = []; s.sizes = []; s.aun = 0; s.abrk = 0; return
    s.names = [nm(e[0]) for e in s.nyfs.names()]; s.sizes = [len(e[1]) for e in s.nyfs.names()]
    mx = max(len(s.names) - frows(480), 0)
    if s.fscr > mx and s.fmode == 0: s.fscr = mx
    audit_check(s)
def nm(b): return b.decode('latin1')
ZERO16 = b"0000000000000000"
def chain_hash(prev, body): return _hl.sha256(prev + body).hexdigest()[:16].encode()
def audit_walk(d):
    """-> (lines, first bad record or 0, last stored hash): written from the log format in the comment of ev.rs, with hashlib"""
    prev = ZERO16; n = 0; bad = 0
    for ln in d.split(b"\n"):
        if not ln: continue
        n += 1; ok = False; p = ln.rfind(b"|")
        if p >= 0:
            body, st = ln[:p], ln[p + 1:]
            ok = len(st) == 16 and len(body) <= 120 and body.startswith(str(n).encode() + b"|") and chain_hash(prev, body) == st
            if len(st) == 16: prev = st
        if not ok and bad == 0: bad = n
    return n, bad, prev
def audit_check(s):
    s.aun = 0; s.abrk = 0
    if not s.ready: return
    st, d = s.nyfs.fread(b"auditlog")
    if st != 0: return
    s.aun, s.abrk, _ = audit_walk(d)
def audit_append(s, text):
    if not s.ready: return
    st, d = s.nyfs.fread(b"auditlog")
    if st == -15:
        if s.nyfs.create(b"auditlog") != 0: return
        d = b""
    elif st != 0: return
    n, _, prev = audit_walk(d)
    body = (str(n + 1).encode() + b"|" + text.encode('latin1')[:120].replace(b"|", b"/"))[:120]
    # (the separator after the sequence number is kept; a '|' inside the text becomes '/')
    body = str(n + 1).encode() + b"|" + text.encode('latin1').replace(b"|", b"/"); body = body[:120]
    h = chain_hash(prev, body); line = body + b"|" + h + b"\n"
    if len(d) + len(line) > 2016: return
    if s.nyfs.fwrite(b"auditlog", d + line) == 0: fs_refresh(s)
def audit_boot(s):
    mode, signer = s.sysv[0] & 255, (s.sysv[0] >> 8) & 255
    audit_append(s, {0: "boot enforcing ", 1: "boot enrollment "}.get(mode, "boot sb-disabled ") + {1: "platform-key", 2: "user-key"}.get(signer, "unsigned"))
def audit_run(s, bi, res):
    if bi >= 8: t = "run %s image %s" % (PROGS[bi][0], "verified" if res == 0 else "corrupt")
    else: t = "run %s C8[EP SHM RPL q2] %s" % (PROGS[bi][0], "loaded" if res == 0 else "refused" if res == -20 else "error")
    if res < 0: t += " -%d" % -res
    audit_append(s, t[:80])
def nfs_call(s, f, *a):
    return -100 if not s.ready else f(*a)
def open_file(s, i):
    s.cur = i; st, d = nfs_call(s, s.nyfs.fread, s.names[i].encode('latin1')) if s.ready else (-100, b"")
    if st == 0: s.buf = d; s.fmsg = 0
    else: s.buf = b""; s.fmsg = 7
    s.dirty = False; s.fscr = 0; s.fmode = 1
def err_msg(st): return {-9: 4, -6: 5, -20: 6, -19: 8}.get(st, 7)
def fs_create(s, n):
    st = nfs_call(s, s.nyfs.create, n)
    if st == 0: fs_refresh(s)
    return st
def files_click(s, i):
    if i == 320: s.fmode = 0; s.fscr = 0; s.dirty = False; s.fmsg = 0; fs_refresh(s)
    elif i == 321: s.fscr = max(s.fscr - (3 if s.fmode == 1 else 1), 0)
    elif i == 322:
        if s.fmode == 1: s.fscr = min(s.fscr + 3, max(nlines(s) - vrows(s.wh[2]), 0))
        else: s.fscr = min(s.fscr + 1, max(len(s.names) - frows(s.wh[2]), 0))
    elif i == 323:
        if s.dirty:
            st = nfs_call(s, s.nyfs.fwrite, s.names[s.cur].encode('latin1'), s.buf)
            if st == 0: s.dirty = False; s.fmsg = 1; fs_refresh(s)
            else: s.fmsg = err_msg(st)
    elif i == 330: s.fmode = 2; s.fin = ""; s.fmsg = 0
    elif 340 <= i <= 359:
        if i - 340 < len(s.names): s.fmsg = 0; open_file(s, i - 340)
    elif 360 <= i <= 379:
        k = i - 360
        if k < len(s.names): st = nfs_call(s, s.nyfs.unlink, s.names[k].encode('latin1')); s.fmsg = 3 if st == 0 else err_msg(st); fs_refresh(s)
import hashlib as _hl
NYGI = [open(os.path.join(HERE, '..', 'userprog', 'nygi', n), 'rb').read() for n in ('demo.nygi', 'bad.nygi')]
def nygi_verify(d, n):
    """-> (code, nfiles): the same rules as desk/src/nygi.rs, written from NPS-006 + tools/make_nygi.py with hashlib"""
    if n < 64 or n > 4096 or len(d) != n: return -1, 0
    if d[:4] != b"NYGI": return -1, 0
    u = lambda o: int.from_bytes(d[o:o + 4], 'little')
    if u(4) != 1 or u(8) != 0: return -2, 0
    nf, mo, co, tl = u(12), u(16), u(20), u(24)
    if nf == 0 or nf > 8 or mo != 64 or co != 64 + 56 * nf or tl != n or co > n: return -3, 0
    if _hl.sha256(d[mo:co]).digest() != d[32:64]: return -4, 0
    for i in range(nf):
        e = mo + 56 * i; off, sz = u(e + 16), u(e + 20)
        if off > n - co or sz > n - co - off: return -5, 0
        if _hl.sha256(d[co + off:co + off + sz]).digest() != d[e + 24:e + 56]: return -6, 0
    return 0, nf
def play(s):
    """the overlay save: the play counter in the NyFS file starlight.sav"""
    if not s.ready: return 0
    st, d = s.nyfs.fread(b"starlight.sav"); n = 0
    if st == 0: n = int(d[:9].decode()) if d[:9].isdigit() else (int("".join(ch for ch in d[:9].decode() if ch.isdigit()) or 0))
    elif st == -15:
        if s.nyfs.create(b"starlight.sav") != 0: return 0
    else: return 0
    n += 1
    if s.nyfs.fwrite(b"starlight.sav", str(n).encode()) != 0: return 0
    fs_refresh(s); return n
def kp_text(s):
    pb = s.sysv[0] >> 24
    ptr = "mouse n/a" if not pb & 1 else ("PS/2 mouse ok" if pb & 2 else "PS/2 mouse no ACK")
    usbn = "+".join(n for k, n in enumerate(["UHCI", "OHCI", "EHCI", "xHCI"]) if (pb >> (2 + k)) & 1)
    kp = ("Nyrqis 1-71 | " + ptr + " | " + (usbn + (" (HID ok)" if pb & 64 else " (no driver)") if usbn else "no USB controller"))[:48]
    return kp
def tpush(s, t):
    if len(s.th) == 24: s.th.pop(0)
    s.th.append(t[:48])
def run(s):
    c = s.ti; s.ti = ""; tpush(s, "nyrqis@desk:~$ " + c); sp = c.find(" "); name = c if sp < 0 else c[:sp]; rest = "" if sp < 0 else c[sp + 1:]
    if not name: return
    if name == "help": tpush(s, "help uname date whoami apps echo clear ls cat"); tpush(s, "write rm audit progs run sys"); tpush(s, "open close layout ps kill spawn")
    elif name == "uname": tpush(s, "Nyrqis OS x86_64 (user-space shell)")
    elif name == "date": tpush(s, s.clock + " (CMOS clock)")
    elif name == "whoami": tpush(s, "nyrqis")
    elif name == "apps":
        for i in range(4): tpush(s, APPN[i] + "  " + {1: "open", 2: "minimised"}.get(s.ws[i], "closed"))
    elif name == "echo": tpush(s, rest)
    elif name == "ls":
        fs_refresh(s)
        if not s.ready: tpush(s, "storage unavailable")
        elif not s.names: tpush(s, "(no files)")
        else:
            for k in range(len(s.names)): tpush(s, (s.names[k] + "  " + str(s.sizes[k]) + " bytes")[:48])
    elif name == "cat":
        st, d = nfs_call(s, s.nyfs.fread, rest.encode('latin1')[:16]) if s.ready else (-100, b"")
        if st == 0:
            a = 0; shown = 0
            for k in range(len(d) + 1):
                if k == len(d) or d[k] == 10:
                    if shown < 6 and not (k == len(d) and a == k and shown > 0): tpush(s, "".join(chr(c) if 32 <= c < 127 else "?" for c in d[a:k][:48])); shown += 1
                    a = k + 1
        else: tpush(s, ("no such file: " + rest))
    elif name == "write":
        sp2 = rest.find(" "); fname = rest if sp2 < 0 else rest[:sp2]; text = "" if sp2 < 0 else rest[sp2 + 1:]
        fb = fname.encode('latin1')[:16]
        fs_refresh(s); st = 0 if fb in [n.encode('latin1') for n in s.names] else fs_create(s, fb)
        if st == 0: st = nfs_call(s, s.nyfs.fwrite, fb, text.encode('latin1')); fs_refresh(s)
        if st == 0: tpush(s, ("wrote " + str(len(text)) + " bytes")[:48])
        else: tpush(s, FMSG[err_msg(st)])
    elif name == "rm":
        st = nfs_call(s, s.nyfs.unlink, rest.encode('latin1')[:16])
        if st == 0: fs_refresh(s); tpush(s, ("removed " + rest))
        else: tpush(s, ("no such file: " + rest))
    elif name == "audit":
        fs_refresh(s)
        st, d = s.nyfs.fread(b"auditlog") if s.ready else (-100, b"")
        if not s.ready: tpush(s, "storage unavailable")
        elif st != 0: tpush(s, "no audit log yet")
        else:
            n, bad, _ = audit_walk(d)
            tpush(s, ("%d events, hash chain intact" % n) if bad == 0 else ("%d events, BROKEN at record %d" % (n, bad)))
            for ln in [l for l in d.split(b"\n") if l][-3:]: tpush(s, "".join(chr(c) if 32 <= c < 127 else "?" for c in ln[:48]))
    elif name == "progs":
        for i in range(10): tpush(s, PROGS[i][0] + "  " + PROGS[i][1])
    elif name == "run":
        m = [i for i in range(10) if rest and PROGS[i][0].startswith(rest)]
        if not m: tpush(s, "no such program: " + rest)
        elif s.arun >= 0 or s.tp: tpush(s, "launcher busy")
        else: i = m[0]; s.arun = i; s.want = i; s.ares[i] = None; s.tr = i; tpush(s, "launching " + PROGS[i][0])
    elif name == "sys":
        mode, signer = s.sysv[0] & 255, (s.sysv[0] >> 8) & 255
        if not s.sysok: tpush(s, "waiting for the kernel...")
        else:
            tpush(s, "secure boot: " + {0: "enforcing", 1: "enrollment"}.get(mode, "disabled") + ", " + {1: "platform key", 2: "user key"}.get(signer, "unsigned"))
            tpush(s, "image: %d bytes %016x" % (s.sysv[2], s.sysv[1]))
            tpush(s, kp_text(s))
    elif name in ("open", "close"):
        m = [i for i in range(4) if rest and (APPN[i].lower().startswith(rest) or (i == 0 and "hub".startswith(rest)))]
        if not m: tpush(s, "no such app: " + rest)
        else:
            i = m[0]
            if name == "open": open_app(s, i)
            else: s.ws[i] = 0
            tpush(s, ("opened " if name == "open" else "closed ") + APPN[i])
    elif name == "layout":
        s.layout = (s.layout + 1) % 3; tpush(s, ["layout: floating", "layout: columns", "layout: grid"][s.layout])
    elif name == "ps":
        if s.arun >= 0 or s.tp: tpush(s, "launcher busy")
        else: s.want = 100; s.tp = 1
    elif name == "kill":
        if rest != "loaders": tpush(s, "usage: kill loaders")
        elif s.arun >= 0 or s.tp: tpush(s, "launcher busy")
        else: s.want = 101; s.tp = 2
    elif name == "spawn":
        if rest != "spin": tpush(s, "usage: spawn spin")
        elif s.arun >= 0 or s.tp: tpush(s, "launcher busy")
        else: s.want = 10; s.tp = 5
    elif name == "clear": s.th = []
    else: tpush(s, "unknown command: " + name)
def event(s, t, a, b):
    """-> (redraw, reply)"""
    if t == 100:
        if b == 102:
            if s.tp == 4: s.tp = 3; s.want = 100
            return True, 0
        if b == 10:
            if s.tp == 5:
                s.tp = 0; tpush(s, "spin started" if a == 0 else "spin failed, code -%d" % abs(a))
            return True, 0
        if b == 100:                                                  # 1-70: the process snapshot (psdata = what the kernel itself reported, parsed from its serial log by the test)
            if s.tp == 3:
                s.tp = 0; s.pl = []
                for i, (alive, cont, blk, pages, name) in enumerate(s.psdata[:88]):
                    if not alive or len(s.pl) >= 16: continue
                    s.pl.append([i, cont, blk, min(pages, 255)] + list(name.encode()[:8].ljust(8, b"\0")))
                s.pn = len(s.pl)
            elif s.tp == 1:
                s.tp = 0; live = 0
                for i, (alive, cont, blk, pages, name) in enumerate(s.psdata[:88]):
                    if not alive: continue
                    live += 1; tpush(s, "%d %s C%d %dpg%s" % (i, name or "root", cont, min(pages, 255), " blk" if blk else ""))
                tpush(s, "%d live of %d started" % (live, min(len(s.psdata), 88)))
            return True, 0
        if b == 101:
            if s.tp == 2: s.tp = 0; tpush(s, "%d loader process(es) killed" % a) if a >= 0 else tpush(s, "kill failed -%d" % -a)
            return True, 0
        if 0 <= b < 10:
            res = a
            if b >= 8:
                if a > 0:
                    res, nf = nygi_verify(NYGI[b - 8][:a] if b - 8 < len(NYGI) else b"", a); s.anf[b] = nf
                    if res == 0: s.apl[b] = play(s)
                elif a == 0: res = -1
            if b < 8 and res == 0 and s.pout >> 63: s.aret[b] = ((s.pout & 0xffffffff) ^ 0x80000000) - 0x80000000; s.aok |= 1 << b
            else: s.aok &= ~(1 << b)
            s.ares[b] = res; audit_run(s, b, res)
            if s.arun == b: s.arun = -1
            if s.tr == b:
                s.tr = -1
                if b >= 8: v = ("verified, %d files" % s.anf[b]) if res == 0 else "corrupt (-%d)" % -res
                else: v = (("loaded, exit code %d" if b < 4 else "loaded, returned %d") % s.aret[b] if s.aok >> b & 1 else "loaded") if res == 0 else "refused (-20)" if res == -20 else "error %d" % abs(res)
                tpush(s, PROGS[b][0] + ": " + v)
            return True, 0
        return False, 0
    if t == 1:
        s.mx, s.my = clamp(a, 0, 1023), clamp(b, 0, 767); s.down = True; i = hit(s, s.mx, s.my); was = s.ov; reply = 0 if i == -1 else i + 0x100
        if not inpanel(was, i) and i not in (60, 61, 1): s.ov = 0
        if i == 1: s.ov = 0 if was == 1 else 1; s.q = ""
        elif i == 60: s.ov = 0 if was == 2 else 2
        elif i == 61: s.ov = 0 if was == 3 else 3
        elif i == 6: s.layout = (s.layout + 1) % 3
        elif 10 <= i <= 13:
            k = i - 10
            if s.ws[k] == 1 and top(s) == k: s.ws[k] = 2
            else: open_app(s, k)
        elif 50 <= i <= 53: open_app(s, i - 50)
        elif 30 <= i <= 33: open_app(s, i - 30); s.ov = 0
        elif 62 <= i <= 64: s.dism |= 1 << (i - 62)
        elif i == 65: s.dism = 7
        elif 70 <= i <= 75: s.tog ^= 1 << (i - 70)
        elif i in (76, 77): k = 2 if i == 76 else 3; s.drag = (k, 0, 0); (setattr(s, 'br', qs_val(s.mx)) if k == 2 else setattr(s, 'vol', qs_val(s.mx)))
        elif 100 <= i <= 139:
            k = (i - 100) // 10; p = (i - 100) % 10
            if p == 0: focus(s, k)
            elif p == 1: s.ws[k] = 0
            elif p == 2: s.ws[k] = 2
            elif p == 3: s.layout = (s.layout + 1) % 3
            elif p == 5:
                focus(s, k)
                if s.layout == 0: s.drag = (4, s.ww[k] - s.mx, s.wh[k] - s.my)
            else:
                focus(s, k)
                if s.layout == 0: s.drag = (1, s.mx - s.wx[k], s.my - s.wy[k])
        elif 320 <= i <= 379: files_click(s, i)
        elif 400 <= i <= 409:
            if s.arun < 0: s.arun = i - 400; s.want = i - 400; s.ares[i - 400] = None
        elif i == 430: s.hmode = 0
        elif i in (431, 432):
            s.hmode = 1
            if s.arun < 0 and s.tp == 0: s.want = 100; s.tp = 3
        elif 440 <= i <= 455:
            k = i - 440
            if k < s.pn and s.arun < 0 and s.tp == 0: s.want = 200 + s.pl[k][0]; s.tp = 4
        elif i == 410: s.ascr = max(s.ascr - 1, 0)
        elif i == 411: s.ascr = min(s.ascr + 1, max(10 - min(frows(s.wh[0]), 10), 0))
        refresh_hover(s); return True, reply
    if t == 6:
        x, y = clamp(a, 0, 1023), clamp(b, 0, 767)
        if (x, y) == (s.mx, s.my): return False, 0
        s.mx, s.my = x, y
        if s.down and s.drag[0] == 1:
            i = s.z[3]; s.wx[i] = clamp(x - s.drag[1], -(s.ww[i] - 120), 1024 - 120); s.wy[i] = clamp(y - s.drag[2], 41, 700); s.hover = -1; return True, 0
        if s.down and s.drag[0] == 4:
            i = s.z[3]; s.ww[i] = clamp(x + s.drag[1], 360, max(360, 1024 - s.wx[i])); s.wh[i] = clamp(y + s.drag[2], 260, max(260, 768 - s.wy[i])); s.hover = -1; return True, 0
        if s.down and s.drag[0] >= 2:
            (setattr(s, 'br', qs_val(x)) if s.drag[0] == 2 else setattr(s, 'vol', qs_val(x))); s.hover = -1; return True, 0
        old = s.hover; refresh_hover(s); return old != s.hover, 0
    if t == 15: s.down = False; s.drag = (0, 0, 0); s.mx, s.my = clamp(a, 0, 1023), clamp(b, 0, 767); refresh_hover(s); return True, 0
    if t == 16: s.ov = 0 if s.ov == 1 else 1; s.q = ""; refresh_hover(s); return True, 0
    if t == 3:
        if s.ov == 0 and top(s) == 2 and s.fmode != 0: files_click(s, 320); refresh_hover(s); return True, 0
        s.ov = 0; refresh_hover(s); return True, 0
    if t == 5:
        if s.ov == 1:
            c = 0
            if a < 26: c = ord('a') + a
            elif 32 <= a < 58: c = ord('a') + a - 32
            elif a == 26: c = ord(' ')
            if c and len(s.q) < 24: s.q += chr(c)
            elif a == 255 and s.q: s.q = s.q[:-1]
            refresh_hover(s); return True, 0
        if s.ov == 0 and top(s) == 2 and s.fmode != 0:
            if s.fmode == 2:
                c = 0
                if a < 26: c = ord('a') + a
                elif 32 <= a < 58: c = ord('a') + a - 32
                if c and len(s.fin) < 15: s.fin += chr(c); return True, 0
                if a == 255 and s.fin: s.fin = s.fin[:-1]; return True, 0
                return False, 0
            c = 0
            if a < 26: c = ord('A') + a
            elif 32 <= a < 58: c = ord('a') + a - 32
            elif a == 26: c = ord(' ')
            if c and len(s.buf) < 2016: s.buf += bytes([c]); s.dirty = True; s.fmsg = 0; return True, 0
            if c: s.fmsg = 8; return True, 0
            if a == 255 and s.buf: s.buf = s.buf[:-1]; s.dirty = True; s.fmsg = 0; return True, 0
            return False, 0
        if s.ov == 0 and top(s) == 3:
            c = 0
            if a < 26: c = ord('A') + a
            elif 32 <= a < 58: c = ord('a') + a - 32
            elif a == 26: c = ord(' ')
            if c and len(s.ti) < 30: s.ti += chr(c); return True, 0
            if a == 255 and s.ti: s.ti = s.ti[:-1]; return True, 0
        return False, 0
    if t == 2:
        if a in (1, 2) and s.ov == 0:
            v = [i for i in s.z if s.ws[i] == 1]
            if len(v) < 2: return False, 0
            if a == 1: focus(s, v[0])
            else: s.z = [v[-1]] + [i for i in s.z if i != v[-1]]
            refresh_hover(s); return True, 0
        if a == 3 and s.ov == 0 and top(s) == 2 and s.fmode == 2:
            n = s.fin; l = len(n)
            if l == 0: s.fmsg = 6; s.fmode = 0; return True, 0
            st = fs_create(s, n.encode('latin1'))
            if st == 0:
                if n in s.names: open_file(s, s.names.index(n))
                else: s.fmode = 0; s.fmsg = 7
            else: s.fmode = 0; s.fmsg = err_msg(st)
            return True, 0
        if a == 3 and s.ov == 0 and top(s) == 2 and s.fmode == 1 and len(s.buf) < 2016: s.buf += b"\n"; s.dirty = True; s.fmsg = 0; return True, 0
        if a == 3 and s.ov == 0 and top(s) == 3: run(s); return True, 0
        if a == 3 and s.ov == 1:
            m = matches(s)
            if m: open_app(s, m[0]); s.ov = 0; refresh_hover(s); return True, 0
        return False, 0
    if t == 14:
        hh, mm = a >> 8, a & 255
        if hh < 24 and mm < 60: s.clock = "%02d:%02d" % (hh, mm); return True, 0
        return False, 0
    return False, 0
# ---------------------------------------------------------------- scene
def hv(s, i): return s.hover == i
def hl(c, on, cx, cy, hs, r):
    if on: c.rrect(cx - hs, cy - hs, hs * 2, hs * 2, r, WHITE, WHITE, 24, WHITE, 0)
def draw(c, s):
    c.rect(0, 0, 1024, 40, 0, 110); c.rect(0, 40, 1024, 1, WHITE, 18)
    if hv(s, 60) or s.ov == 2: c.rrect(16, 4, 170, 32, 16, WHITE, WHITE, 16, WHITE, 0)
    if hv(s, 61) or s.ov == 3: c.rrect(786, 4, 224, 32, 16, WHITE, WHITE, 16, WHITE, 0)
    c.icon(I['I_NOTIFICATIONS_18'], 27, 11, TXT, 255)
    if ncount(s) > 0: c.rrect(56, 10, 20, 20, 10, 0x4F46E5, 0x4F46E5, 255, WHITE, 0); tw = text_w(G['M11'], str(ncount(s))); c.text(G['M11'], 66 - tw // 2, 24, str(ncount(s)), WHITE, 255)
    c.text(G['M13'], 82, 25, "Notifications", TXT, 255); c.icon(I['I_KEYBOARD_ARROW_DOWN_18'], 166, 11, DIM, 255)
    c.rrect(474, 18, 36, 4, 2, WHITE, WHITE, 60, WHITE, 0)
    c.icon(I['I_WIFI_16'], 800, 12, 0x34D399, 255 if s.tog & 1 else 70); c.icon(I['I_BLUETOOTH_16'], 826, 12, BLUE, 255 if s.tog >> 1 & 1 else 70); c.icon(I['I_SPORTS_ESPORTS_16'], 850, 12, PURPLE, 255 if s.tog >> 2 & 1 else 70)
    c.icon(I['I_BATTERY_CHARGING_FULL_16'], 874, 12, 0x34D399, 255); c.text(G['M12'], 896, 25, "88%", TXT, 255); c.rect(926, 13, 1, 14, WHITE, 50)
    c.text(G['M13'], 938, 25, s.clock, WHITE, 255); c.icon(I['I_KEYBOARD_ARROW_DOWN_18'], 984, 11, DIM, 255)
    for i in range(4):
        y = DESK_Y[i]; h = hv(s, 50 + i); c.rrect(924, y, 56, 56, 14, WHITE, WHITE, 26 if h else 12, WHITE, 60 if h else 22); c.icon(APPI28[i], 938, y + 14, APPC[i], 255)
        w = text_w(G['M12'], APPN[i]) + 20; c.rrect(952 - w // 2, y + 62, w, 22, 11, 0, 0, 110, WHITE, 0); tw = text_w(G['M12'], APPN[i]); c.text(G['M12'], 952 - tw // 2, y + 77, APPN[i], TXT, 255)
    for k in range(4):
        i = s.z[k]
        if s.ws[i] == 1: window(c, s, i, all(s.ws[s.z[j]] != 1 for j in range(k + 1, 4)))
    c.rrect(DOCK[0], DOCK[1], DOCK[2], DOCK[3], 29, 0x0F1220, 0x0F1220, 205, WHITE, 24)
    for id_, cx in ((1, DOCK_X[0]), (10, DOCK_X[1]), (11, DOCK_X[2]), (12, DOCK_X[3]), (13, DOCK_X[4]), (6, DOCK_VIEW)): hl(c, hv(s, id_), cx, 729, 22, 12)
    c.icon(I['I_GRID_VIEW_24'], DOCK_X[0] - 12, 717, BLUE, 255); c.rect(411, 715, 1, 28, WHITE, 40)
    for i in range(4):
        c.icon(APPI24[i], DOCK_X[i + 1] - 12, 717, APPC[i], 255)
        if s.ws[i] == 1: c.rrect(DOCK_X[i + 1] - 3, 750, 6, 6, 3, APPC[i], APPC[i], 255, WHITE, 0)
        elif s.ws[i] == 2: c.rrect(DOCK_X[i + 1] - 2, 751, 4, 4, 2, APPC[i], APPC[i], 110, WHITE, 0)
    c.rect(621, 715, 1, 28, WHITE, 40); c.icon(I['I_VIEW_QUILT_18'], DOCK_VIEW - 9, 720, PURPLE if s.layout > 0 else DIM, 255)
    if s.ov == 1: start(c, s)
    elif s.ov == 2: notif(c, s)
    elif s.ov == 3: qs(c, s)
    c.clearclip()
    if s.tog >> 4 & 1: c.rect(0, 0, 1024, 768, 0xFF9628, 28)
    if s.br < 100: c.rect(0, 0, 1024, 768, 0, 100 - s.br)
def tc(c, face, cx, base, st, rgb, a): c.text(face, cx - text_w(face, st) // 2, base, st, rgb, a)
def pill(c, x, y, w, on, hot, col, label):
    if on: c.rrect(x, y, w, 30, 15, col, col, 140 if hot else 90, col, 150); tc(c, G['M13'], x + w // 2, y + 20, label, WHITE, 255)
    else: c.rrect(x, y, w, 30, 15, WHITE, WHITE, 26 if hot else 12, WHITE, 40); tc(c, G['M13'], x + w // 2, y + 20, label, TXT if hot else DIM, 255)
def procs(c, s, x, y, w, h):
    R12, R13, M12, M14 = (G[k] for k in ('R12', 'R13', 'M12', 'M14'))
    pill(c, x + w - 100, y + 52, 76, False, hv(s, 432), PURPLE, "Refresh")
    if s.pn == 0: tc(c, R13, x + w // 2, y + 150, "No snapshot yet. Press Refresh.", DIM, 255)
    for k in range(min(frows(h), 16)):
        if k >= s.pn: break
        r = s.pl[k]; ry = y + 98 + 38 * k
        c.rrect(x + 24, ry, w - 48, 34, 10, WHITE, WHITE, 8, WHITE, 20)
        nm = bytes(r[4:12]).split(b"\0")[0].decode()
        c.text(M14, x + 40, ry + 22, ("%d  %s" % (r[0], nm or "root"))[:48], WHITE, 255)
        t2 = ("C%d  %dpg%s" % (r[1], r[3], " blk" if r[2] else ""))[:48]
        if w >= 480: c.text(R12, x + 200, ry + 22, t2, DIM, 255)
        if killable(r):
            rx = x + w - 24 - 12 - 62; hot = hv(s, 440 + k)
            c.rrect(rx, ry + 5, 62, 24, 12, 0xF87171, 0xF87171, 140 if hot else 70, 0xF87171, 120); tc(c, M12, rx + 31, ry + 21, "Kill", WHITE, 255)
def files(c, s, x, y, w, h):
    R12, R13, M13, M14 = (G[k] for k in ('R12', 'R13', 'M13', 'M14'))
    if not s.ready: tc(c, R13, x + w // 2, y + h // 2, "Storage service unavailable", DIM, 255); return
    if s.fmode == 0:
        pill(c, x + 24, y + 52, 112, True, hv(s, 330), PURPLE, "+ New file")
        if w >= 420: c.text(R12, x + 152, y + 72, "%d of 20 files" % len(s.names), DIM, 255)
        nr = frows(h)
        if len(s.names) > nr:
            for cx, up in ((x + w - 60, True), (x + w - 28, False)):
                c.rrect(cx - 14, y + 53, 28, 28, 8, WHITE, WHITE, 26 if hv(s, 321 if up else 322) else 10, WHITE, 0); c.icon(I['I_KEYBOARD_ARROW_UP_18'] if up else I['I_KEYBOARD_ARROW_DOWN_18'], cx - 9, y + 58, TXT, 255)
        if not s.names: tc(c, R13, x + w // 2, y + 150, "No files yet. Create one with + New file.", DIM, 255)
        for k in range(nr):
            a = s.fscr + k
            if a >= len(s.names): break
            ry = y + 98 + 38 * k
            c.rrect(x + 24, ry, w - 48, 34, 10, WHITE, WHITE, 26 if hv(s, 340 + a) else 8, WHITE, 20); c.icon(I['I_DESCRIPTION_24'], x + 36, ry + 5, BLUE, 255)
            c.text(M14, x + 72, ry + 22, s.names[a], WHITE, 255)
            if w >= 360: c.text(R12, x + w - 150, ry + 22, "%d bytes" % s.sizes[a], DIM, 255)
            hl(c, hv(s, 360 + a), x + w - 44, ry + 17, 14, 8); c.icon(I['I_CLOSE_18'], x + w - 53, ry + 8, WHITE if hv(s, 360 + a) else DIM, 255)
        if s.fmsg: c.text(R12, x + 24, y + h - 12, FMSG[s.fmsg], 0xF87171 if s.fmsg >= 4 else TEAL, 255)
    elif s.fmode == 1:
        c.rrect(x + 24, y + 52, 76, 30, 15, WHITE, WHITE, 26 if hv(s, 320) else 12, WHITE, 40); c.icon(I['I_ARROW_BACK_18'], x + 34, y + 58, TXT, 255); c.text(M13, x + 58, y + 72, "Back", TXT, 255)
        nw = c.text(M13, x + 112, y + 72, s.names[s.cur], WHITE, 255)
        if s.dirty: c.text(M13, x + 112 + nw + 6, y + 72, "*", TEAL, 255)
        elif s.fmsg: c.text(R12, x + 112 + nw + 12, y + 72, FMSG[s.fmsg], 0xF87171 if s.fmsg >= 4 else TEAL, 255)
        pill(c, x + w - 100, y + 52, 76, s.dirty, hv(s, 323), TEAL, "Save")
        c.rrect(x + 24, y + 96, w - 48, h - 110, 10, 0, 0, 120, WHITE, 16)
        nr = vrows(h); nl = nlines(s); c.setclip(x + 26, y + 98, w - 52 - 40, h - 114)
        for k in range(nr):
            li = s.fscr + k
            if li >= nl: break
            t = vline(s, li); base = y + 118 + 20 * k; tw = c.text(R13, x + 36, base, t, TXT, 255)
            if li == nl - 1: c.rect(x + 37 + tw, base - 12, 2, 15, TEAL, 255)
        c.setclip(x + 1, y + 1, w - 2, h - 2)
        for cy, up in ((y + 110, True), (y + h - 28, False)):
            c.rrect(x + w - 54, cy - 14, 28, 28, 8, WHITE, WHITE, 26 if hv(s, 321 if up else 322) else 10, WHITE, 0); c.icon(I['I_KEYBOARD_ARROW_UP_18'] if up else I['I_KEYBOARD_ARROW_DOWN_18'], x + w - 49, cy - 9, TXT, 255)
    else:
        c.rrect(x + 24, y + 52, w - 48, 32, 16, WHITE, WHITE, 10, PURPLE, 150); lw = c.text(M13, x + 40, y + 73, "New file name:", DIM, 255)
        tw = c.text(M13, x + 40 + lw + 8, y + 73, s.fin, WHITE, 255); c.rect(x + 40 + lw + 9 + tw, y + 60, 2, 16, TEAL, 255)
        c.text(R12, x + 24, y + 112, "Letters only, up to 15. Enter creates the file, Esc cancels.", DIM, 255)
        if s.fmsg: c.text(R12, x + 24, y + 136, FMSG[s.fmsg], 0xF87171, 255)
def window(c, s, app, focused):
    x, y, w, h = wrect(s, app); c.clearclip()
    c.rrect(x - 6, y - 4, w + 12, h + 14, 22, 0, 0, 80 if focused else 40, WHITE, 0); c.rrect(x, y, w, h, 16, 0x0D1020, 0x090B18, 242, WHITE, 44 if focused else 22)
    c.setclip(x + 1, y + 1, w - 2, h - 2)
    c.rrect(x + 1, y + 1, w - 2, 40, 15, WHITE, WHITE, 8, WHITE, 0); c.rect(x + 1, y + 24, w - 2, 17, 0x0D1020, 0); c.rect(x + 1, y + 40, w - 2, 1, WHITE, 22)
    c.text(G['M13'], x + 18, y + 25, APPN[app], TXT, 255); bid = 100 + 10 * app
    hl(c, hv(s, bid + 3), x + w - 89, y + 20, 14, 8); hl(c, hv(s, bid + 2), x + w - 62, y + 20, 14, 8)
    if hv(s, bid + 1): c.rrect(x + w - 42, y + 6, 28, 28, 8, 0xEF4444, 0xEF4444, 200, WHITE, 0)
    c.icon(I['I_VIEW_QUILT_18'], x + w - 98, y + 11, PURPLE if s.layout > 0 else DIM, 255); c.icon(I['I_REMOVE_18'], x + w - 71, y + 11, DIM, 255); c.icon(I['I_CLOSE_18'], x + w - 37, y + 11, TXT, 255)
    R12, R13, R14, M11, M12, M13, M14, M16 = (G[k] for k in ('R12', 'R13', 'R14', 'M11', 'M12', 'M13', 'M14', 'M16'))
    if app == 0:
        pill(c, x + 24, y + 52, 92, s.hmode == 0, hv(s, 430), PURPLE, "Programs"); pill(c, x + 124, y + 52, 100, s.hmode == 1, hv(s, 431), PURPLE, "Processes")
        if s.hmode == 1: procs(c, s, x, y, w, h)
        nr = 0 if s.hmode == 1 else min(frows(h), 10)
        if s.hmode == 0 and nr < 10:
            for cx, up in ((x + w - 60, True), (x + w - 28, False)):
                c.rrect(cx - 14, y + 53, 28, 28, 8, WHITE, WHITE, 26 if hv(s, 410 if up else 411) else 10, WHITE, 0); c.icon(I['I_KEYBOARD_ARROW_UP_18'] if up else I['I_KEYBOARD_ARROW_DOWN_18'], cx - 9, y + 58, TXT, 255)
        for k in range(nr):
            a = s.ascr + k
            if a >= 10: break
            ry = y + 98 + 38 * k; hot = hv(s, 400 + a)
            c.rrect(x + 24, ry, w - 48, 34, 10, WHITE, WHITE, 26 if hot else 8, WHITE, 20); c.icon(I['I_GRID_VIEW_24'] if a < 4 else I['I_SPORTS_ESPORTS_24'] if a < 8 else I['I_FOLDER_24'], x + 36, ry + 5, BLUE if a < 4 else 0x34D399 if a < 8 else AMBER, 255)
            c.text(M14, x + 72, ry + 22, PROGS[a][0], WHITE, 255)
            if w >= 480: c.text(R12, x + 200, ry + 22, PROGS[a][1], DIM, 255)
            r_ = s.ares[a]
            if s.arun == a: t, col = "Launching...", DIM
            elif r_ is None: t, col = "", DIM
            elif a >= 8 and r_ == 0: t, col = "Verified, %d files, play #%d" % (s.anf[a], s.apl[a]), TEAL
            elif a >= 8 and r_ < 0: t, col = "Corrupt (-%d)" % -r_, 0xF87171
            elif r_ == 0: t, col = ((("Loaded, exit code %d" if a < 4 else "Loaded, returned %d") % s.aret[a]) if s.aok >> a & 1 else "Loaded"), TEAL
            elif r_ == -20: t, col = "Refused (-20)", 0xF87171
            else: t, col = ("Error -%d" % -r_ if r_ < 0 else "Result %d" % r_), 0xF87171
            if t: c.text(R12, x + w - 24 - 12 - 62 - 12 - text_w(R12, t), ry + 22, t, col, 255)
            rx = x + w - 24 - 12 - 62; c.rrect(rx, ry + 5, 62, 24, 12, PURPLE, PURPLE, 140 if hot else 70, PURPLE, 120); tc(c, M12, rx + 31, ry + 21, "Run", WHITE, 255)
    elif app == 1:
        mode, signer = s.sysv[0] & 255, (s.sysv[0] >> 8) & 255
        if not s.sysok: sbt = "waiting for the kernel..."
        elif mode == 2: sbt = "Disabled by the user -- image NOT verified"
        elif mode == 1: sbt = "Enrollment -- user key accepted" if signer == 2 else "Enrollment -- platform key"
        else: sbt = "Enforcing -- user-enrolled key" if signer == 2 else "Enforcing -- signed by the platform key"
        bit = "waiting for the kernel..." if not s.sysok else "%d bytes  SHA-256 %016x" % (s.sysv[2], s.sysv[1])
        stt = "Storage service unavailable" if not s.ready else "NyFS volume at LBA 1900: %d of 20 files" % len(s.names)
        known = [r for r in s.ares if r is not None]
        pg = "none run yet (Console Hub)" if not known else "%d run: %d loaded, %d refused" % (len(known), sum(1 for r in known if r == 0), sum(1 for r in known if r != 0))
        if s.ares[8] is None: gi = "none opened yet (Console Hub)"
        elif s.ares[8] == 0: gi = ("demo.nygi: %d files verified, %d plays in overlay" % (s.anf[8], s.apl[8]))[:48]
        else: gi = "demo.nygi FAILED verification (-%d)" % -s.ares[8]
        if not s.ready: au = "storage unavailable"
        elif s.aun == 0: au = "no events yet"
        elif s.abrk == 0: au = "%d events, hash chain intact" % s.aun
        else: au = "%d events, BROKEN at record %d" % (s.aun, s.abrk)
        kp = kp_text(s)
        rows = [("Secure boot", sbt), ("Boot image (measured)", bit), ("Kernel, pointer, USB", kp), ("Storage", stt), ("Programs", pg), ("Game image (.nygi, read-only + overlay)", gi), ("Audit log (hash-chained, file auditlog)", au), ("Time", s.clock + " (CMOS clock, via rtcd)")]
        for k, r in enumerate(rows):
            ry = y + 56 + 46 * k; c.rrect(x + 24, ry, w - 48, 40, 10, WHITE, WHITE, 8, WHITE, 20); c.text(R12, x + 40, ry + 16, r[0], DIM, 255); c.text(M13, x + 40, ry + 32, r[1], WHITE, 255)
    elif app == 2: files(c, s, x, y, w, h)
    else:
        c.rrect(x + 1, y + 41, w - 2, h - 42, 15, 0, 0, 120, WHITE, 0); c.text(R14, x + 20, y + 74, "Nyrqis OS terminal -- type help", DIM, 255)
        rows = (h - 14 - 98) // 22 + 1; show = min(max(rows - 1, 0), len(s.th))
        for k in range(show):
            ln = s.th[len(s.th) - show + k]; c.text(R14, x + 20, y + 98 + 22 * k, ln, TEAL if ln.startswith("nyrqis@") else TXT, 255)
        py = y + 98 + 22 * show; pw = c.text(R14, x + 20, py, "nyrqis@desk:~$ ", TEAL, 255); iw = c.text(R14, x + 20 + pw, py, s.ti, WHITE, 255); c.rect(x + 22 + pw + iw, py - 12, 2, 15, TEAL, 255)
    if s.layout == 0:
        for dx, dy in ((6, 4), (10, 4), (14, 4), (6, 8), (10, 8), (6, 12)): c.rect(x + w - dx - 2, y + h - dy - 2, 2, 2, WHITE, 220 if hv(s, bid + 5) else 90)
    c.clearclip()
def start(c, s):
    x, y, w, h = START; R12, R13, M12, M13 = (G[k] for k in ('R12', 'R13', 'M12', 'M13'))
    c.rrect(x - 8, y - 4, w + 16, h + 18, 30, 0, 0, 80, WHITE, 0); c.rrect(x, y, w, h, 24, 0x161828, 0x10121F, 238, WHITE, 26)
    c.rrect(x + 24, y + 24, w - 48, 40, 20, WHITE, WHITE, 10, WHITE, 70 if s.q else 36); c.icon(I['I_SEARCH_20'], x + 40, y + 34, DIM, 255)
    if not s.q: c.text(R13, x + 68, y + 49, "Search apps, games, settings, or web...", DIM, 255)
    else: tw = c.text(R13, x + 68, y + 49, s.q, WHITE, 255); c.rect(x + 68 + tw + 1, y + 36, 1, 16, WHITE, 255)
    c.text(M12, x + 28, y + 98, "RESULTS" if s.q else "PINNED APPS", 0xCBD5E1, 255); c.text(M12, x + w - 28 - text_w(M12, "All Apps  ") - 4, y + 98, "All Apps", 0x818CF8, 255); c.icon(I['I_CHEVRON_RIGHT_14'], x + w - 38, y + 86, 0x818CF8, 255)
    m = matches(s)
    for j, i in enumerate(m):
        cx = x + 71 + 126 * j; c.rrect(cx - 24, y + 126, 48, 48, 14, APPC[i], APPC[i], 80 if hv(s, 30 + i) else 40, APPC[i], 60); c.icon(APPI24[i], cx - 12, y + 138, APPC[i], 255); c.text(M12, cx - text_w(M12, APPN[i]) // 2, y + 196, APPN[i], TXT, 255)
    if not m: c.text(R13, x + w // 2 - text_w(R13, "No results") // 2, y + 160, "No results", DIM, 255)
    c.rect(x + 24, y + 235, w - 48, 1, WHITE, 24)
    c.rrect(x + 26, y + 258, 36, 36, 18, 0x8B5CF6, 0x6D28D9, 255, WHITE, 0); c.text(M12, x + 44 - text_w(M12, "NY") // 2, y + 281, "NY", WHITE, 255)
    c.text(M13, x + 74, y + 272, "Nyrqis User", WHITE, 255); c.text(R12, x + 74, y + 289, "Nyrqis OS Administrator", DIM, 255)
    hl(c, hv(s, 41), x + w - 80, y + 278, 18, 10); hl(c, hv(s, 40), x + w - 38, y + 278, 18, 10)
    c.icon(I['I_SETTINGS_20'], x + w - 90, y + 268, DIM, 255); c.icon(I['I_POWER_SETTINGS_NEW_20'], x + w - 48, y + 268, 0xA78BFA, 255)
def notif(c, s):
    x, y, w, h = NOTIF; R12, R13, M12, M13, M14 = (G[k] for k in ('R12', 'R13', 'M12', 'M13', 'M14'))
    c.rrect(x - 8, y - 4, w + 16, h + 18, 30, 0, 0, 80, WHITE, 0); c.rrect(x, y, w, h, 24, 0x161828, 0x10121F, 238, WHITE, 26)
    c.text(M14, x + 20, y + 34, "Notifications", WHITE, 255)
    if ncount(s) > 0: c.text(M12, x + w - 20 - text_w(M12, "Clear all"), y + 32, "Clear all", 0xC7D2FE if hv(s, 65) else 0x818CF8, 255)
    for i in range(3):
        sl = notif_slot(s, i)
        if sl is None: continue
        n = NOTIFS[i]; iy = y + 56 + sl * 72; ix = x + 12
        c.rrect(ix, iy, 356, 64, 14, WHITE, WHITE, 14 if hv(s, 62 + i) else 8, WHITE, 18); c.rrect(ix + 12, iy + 14, 36, 36, 18, n[3], n[3], 60, n[3], 110)
        c.text(M13, ix + 60, iy + 27, n[0], WHITE, 255); c.text(R12, ix + 60, iy + 46, n[1], DIM, 255); c.text(R12, ix + 356 - 64 - text_w(R12, n[2]) // 2, iy + 27, n[2], DIM, 255)
        hl(c, hv(s, 62 + i), ix + 356 - 22, iy + 32, 14, 8); c.icon(I['I_CLOSE_14'], ix + 356 - 29, iy + 25, DIM, 255)
    if ncount(s) == 0: c.text(R13, x + w // 2 - text_w(R13, "You're all caught up") // 2, y + 170, "You're all caught up", DIM, 255)
def qs(c, s):
    x, y, w, h = QS; R12, M12 = G['R12'], G['M12']
    c.rrect(x - 8, y - 4, w + 16, h + 18, 30, 0, 0, 80, WHITE, 0); c.rrect(x, y, w, h, 24, 0x161828, 0x10121F, 238, WHITE, 26)
    for i in range(6):
        on = s.tog >> i & 1; tx = x + 16 + (i % 2) * 160; ty = y + 16 + (i // 2) * 72; hh = hv(s, 70 + i)
        if on: c.rrect(tx, ty, 152, 64, 16, 0x7E22CE, 0x7E22CE, 130 if hh else 100, PURPLE, 150)
        else: c.rrect(tx, ty, 152, 64, 16, WHITE, WHITE, 18 if hh else 10, WHITE, 24)
        c.icon(TILEI[i], tx + 16, ty + 22, 0xE9D5FF if on else DIM, 255); c.text(M12, tx + 46, ty + 29, TILES[i], WHITE if on else TXT, 255); c.text(R12, tx + 46, ty + 47, "On" if on else "Off", 0xE9D5FF if on else DIM, 255)
    for cy, v, ic in ((264, s.br, I['I_BRIGHTNESS_6_20']), (304, s.vol, I['I_VOLUME_UP_20'])):
        c.icon(ic, x + 18, y + cy - 10, DIM, 255); c.rrect(x + 56, y + cy - 3, 236, 6, 3, WHITE, WHITE, 30, WHITE, 0); fw = max(236 * v // 100, 6)
        c.rrect(x + 56, y + cy - 3, fw, 6, 3, 0xA855F7, 0xA855F7, 255, WHITE, 0); c.rrect(x + 56 + 236 * v // 100 - 8, y + cy - 8, 16, 16, 8, WHITE, WHITE, 255, WHITE, 0)
        c.text(R12, x + 300, y + cy + 4, "%d%%" % v, TXT, 255)
CUR = ["X...........", "XX..........", "X.X.........", "X..X........", "X...X.......", "X....X......", "X.....X.....", "X......X....", "X.......X...", "X........X..", "X.....XXXXX.", "X..X..X.....", "X.X.X..X....", "XX..X..X....", "X....X..X...", ".....X..X...", "......XX...."]
def frame(s):
    """the whole screen (H, W, 3) uint8 for the state, with the pointer sprite"""
    global WALL
    if WALL is None: WALL = wallpaper()
    c = Canvas(); c.a[:] = WALL; draw(c, s); out = c.a.astype(np.uint8)
    for j, row in enumerate(CUR):
        inside_ = False
        for i, ch in enumerate(row):
            px, py = s.mx + i, s.my + j
            if not (0 <= px < W and 0 <= py < H): continue
            if ch == 'X': inside_ = not inside_; out[py, px] = (0, 0, 0)
            elif inside_: out[py, px] = (255, 255, 255)
    return out
if __name__ == '__main__':
    s = St(); s.clock = "11:32"; f0 = frame(s)
    assert hit(s, 376, 729) == 1 and hit(s, 562, 98) == 101 and hit(s, 300, 98) == 104 and hit(s, 300, 300) == 403 and hit(s, 300, 495) == 100 and hit(s, 500, 500) == 100 and hit(s, 952, 100) == 50 and hit(s, 100, 650) == -1
    event(s, 1, 376, 729); assert s.ov == 1; event(s, 5, 32 + 5, 0); event(s, 5, 32 + 8, 0); assert s.q == "fi" and matches(s) == [2]; event(s, 2, 3, 0); assert s.ov == 0 and top(s) == 2 and s.z == [0, 1, 3, 2]
    event(s, 1, 562, 98); assert s.ws[0] == 0
    s2 = St(); event(s2, 16, 1, 0); assert s2.ov == 1; event(s2, 3, 0, 0); assert s2.ov == 0
    s3 = St(); [event(s3, 1, 493, 729)] ; assert s3.ws[1] == 1 and top(s3) == 1; event(s3, 1, 493, 729); assert s3.ws[1] == 2 and top(s3) == 0
    s8 = St(); event(s8, 1, 70 + 520 - 5, 78 + 430 - 5); assert s8.drag[0] == 4; event(s8, 6, 70 + 700, 78 + 500); assert (s8.ww[0], s8.wh[0]) == (700 + 5, 500 + 5 - 0) or (s8.ww[0], s8.wh[0]) == (705, 505); event(s8, 6, 0, 0); assert (s8.ww[0], s8.wh[0]) == (360, 260); event(s8, 15, 0, 0); assert s8.drag[0] == 0
    s4 = St(); event(s4, 1, 70 + 300, 78 + 10); event(s4, 6, 400, 200); assert (s4.wx[0], s4.wy[0]) == (70 + 30, 78 + 112) and s4.drag[0] == 1; event(s4, 15, 400, 200); assert not s4.down
    s5 = St(); event(s5, 1, 100, 20); assert s5.ov == 2; event(s5, 1, 16 + 12 + 356 - 22, 48 + 56 + 32); assert s5.dism == 1 and ncount(s5) == 2; event(s5, 1, 16 + 380 - 50, 48 + 20); assert ncount(s5) == 0
    s6 = St(); event(s6, 1, 900, 20); assert s6.ov == 3; event(s6, 1, 664 + 16, 48 + 16); assert s6.tog == 6; event(s6, 1, 664 + 56 + 118, 48 + 264); assert s6.br == 50 and s6.drag[0] == 2; event(s6, 6, 664 + 56 + 236, 48 + 264); assert s6.br == 100
    s7 = St(); s7.ws = [1, 1, 1, 1]; s7.layout = 1; assert [wrect(s7, i)[2] for i in range(4)] == [(892 - 36) // 4] * 4; s7.layout = 2; assert wrect(s7, 0)[3] == (636 - 12) // 2
    print('check_desk self-checks OK; frame hash', hash(f0.tobytes()) & 0xffff)
    if '--png' in sys.argv:
        from PIL import Image
        sts = [St() for _ in range(4)]; event(sts[1], 16, 0, 0); event(sts[2], 1, 100, 20); event(sts[3], 1, 900, 20)
        for k in sts: k.clock = "11:32"
        im = Image.new('RGB', (2048, 1536)); [im.paste(Image.fromarray(frame(k)), ((n % 2) * 1024, (n // 2) * 768)) for n, k in enumerate(sts)]; im.save(sys.argv[sys.argv.index('--png') + 1])
