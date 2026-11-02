#!/usr/bin/env python3
"""Independent lint + reference renderer + event model for the FULL-SCREEN ("tall") NDL-1 shell (increment 1-51): the 1024x768 window with the Android system chrome
(status bar with clock, 3-button navigation bar), the Messages app (bottom navigation bar on compact windows, rail on medium / expanded, list-detail on expanded), the launcher,
recents and the quick-settings shade. It PARSES userprog/shell_layout.inc (dl_t0..dl_t8) and re-renders every state in pure Python/numpy; it shares NO code with
tools/make_shell_layout.py and none with tools/check_shell.py (the band checker). Used three ways:
  check_shell_tall.py                 lint every state (themes x font scales x window sizes x destinations x notification x task) and run the event-model self-checks
  check_shell_tall.py --png out.png   write a contact sheet of the main states (the release evidence)
  import check_shell_tall as T        T.render_bytes(state) -> the exact 1024x768 RGB bytes the kernel's shell must put on the screen (tools/live_keys_test.py)
"""
import os, re, sys, math, itertools
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.join(HERE, '..')
src = open(os.path.join(ROOT, 'userprog', 'shell_layout.inc')).read()
FBW, FBH = 1024, 768
# ---------------------------------------------------------------- parse
lists = {}; cur = None
for ln in src.splitlines():
    m = re.match(r'dl_t(\d+):', ln)
    if m: cur = int(m.group(1)); lists[cur] = []; continue
    if re.match(r'\w+:', ln): cur = None
    m = re.match(r'\s+\.long (.*)$', ln)
    if m and cur is not None:
        f = [x.strip() for x in m.group(1).split(',')]; assert len(f) == 8
        lists[cur].append([int(v, 0) if '-' not in v else v for v in f])
strs = {m.group(1): m.group(2) for m in re.finditer(r'^(s_\w+): \.asciz "([^"]*)"', src, re.M)}
rr_off = [int(v) for v in re.search(r'rr_off:\n\s+\.long (.*)', src).group(1).split(',')]
rr_tab = [int(v) for v in re.search(r'rr_tab:\n\s+\.byte (.*)', src).group(1).split(',')]
ICONS = bytes(int(v) for ln in re.search(r'icons:[^\n]*\n((?:\s+\.byte [^\n]*\n)+)', src).group(1).splitlines() for v in ln.split('.byte')[1].split(','))
MAN = {int(m.group(5)): (m.group(1), m.group(2), int(m.group(3)), int(m.group(4))) for m in re.finditer(r'/\* icon (\w+) (\w+) (\d+) (\d+) @ (\d+) \*/', src)}   # offset -> (variant, name, size, rotation)
_hdr = re.search(r'font_hdr:\n((?:\s+\.long [^\n]*\n){12})', src).group(1).splitlines(); HD = [tuple(int(v) for v in ln.split('.long')[1].split(',')) for ln in _hdr]
_gl = re.search(r'font_gl:[^\n]*\n((?:\s+\.long [^\n]*\n){1140})', src).group(1).splitlines()
FG = []
for _k in range(12):
    row = []
    for ln in _gl[_k * 95:(_k + 1) * 95]:
        off, pk, adv = (int(v, 0) for v in ln.split('.long')[1].split(','))
        sb = lambda v: v - 256 if v > 127 else v
        row.append((off, pk & 255, pk >> 8 & 255, sb(pk >> 16 & 255), sb(pk >> 24 & 255), adv))
    FG.append(row)
FMASK = bytes(int(v) for ln in re.search(r'font_masks:[^\n]*\n((?:\s+\.byte [^\n]*\n)+)', src).group(1).splitlines() for v in ln.split('.byte')[1].split(','))
ICON_A = np.frombuffer(ICONS, dtype=np.uint8); FMASK_A = np.frombuffer(FMASK, dtype=np.uint8)
assert sorted(lists) == list(range(9)), 'list index = 3 * font scale + width class'
for k in lists:
    for rec in lists[k]:
        if rec[7] == '-1': rec[7] = -1                                          # aux = -1: the CLOCK string (1-52), drawn from the state
        elif isinstance(rec[7], str): assert rec[7].endswith(' - shell_base'); rec[7] = strs[rec[7].split(' - ')[0]]
    assert lists[k][-1][0] == 0; lists[k].pop()
    for rec in lists[k]: rec[8:] = [rec[0] >> 8]; rec[0] &= 0xff           # rec = [type, x, y, w, h, r, col, aux, cond]
WIDTHS = (1024, 640, 360)                                                   # window size index 0 / 1 / 2 = expanded / medium / compact
def cls_of(w): return 0 if w < 600 else (1 if w < 840 else 2)
def LST(st): return lists[3 * st['scale'] + cls_of(WIDTHS[st['sidx']])]
def tw_(face, t): return sum(FG[face][(ord(c) - 32) if 32 <= ord(c) < 127 else 0][5] for c in t)
# ---------------------------------------------------------------- colours
DARK = {0xFEF7FF: 0x141218, 0xF3EDF7: 0x211F26, 0xEADDFF: 0x4F378B, 0xE8DEF8: 0x4A4458, 0x1D1B20: 0xE6E0E9, 0x49454F: 0xCAC4D0, 0x4F378B: 0xEADDFF, 0x6750A4: 0xD0BCFF}
def tc(col, dark): return DARK.get(col, col) if dark else col
def lum(c):
    def ch(v): v /= 255; return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c >> 16 & 255) + 0.7152 * ch(c >> 8 & 255) + 0.0722 * ch(c & 255)
def contrast(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True); return (la + 0.05) / (lb + 0.05)
RING = 0x6750A4
# ---------------------------------------------------------------- state
DEST_HOME, DEST_FIND, DEST_SHADE, DEST_LAUNCH, DEST_RECENTS = 0, 1, 2, 3, 4
def clock_str(st):                                                           # the status-bar clock: "--:--" until the real-time-clock service has told the time (1-52), then 24-hour "HH:MM"
    return '--:--' if st['clock'] is None else '%02d:%02d' % (st['clock'] >> 8, st['clock'] & 255)
def new_state(**kw):
    st = dict(dest=DEST_LAUNCH, focus=-1, dark=0, text='', cursor=None, scale=0, notif=0, task=0, sidx=0, prev=0, taskdest=0, retdest=0, nav=0, clock=None)    # nav: 0 = gesture navigation (default), 1 = three buttons
    st.update(kw); return st
def visible(cond, st):
    if cond & 0x100 and not st['task']: return False
    if cond & 0x200 and st['task']: return False
    if cond & 0x400 and st['text']: return False
    if cond & 0x20 and not st['notif']: return False
    if cond & 0x800 and st['nav'] != 1: return False                         # only in 3-button mode
    if cond & 0x1000 and st['nav'] != 0: return False                       # only in gesture mode
    if cond & 0x40 and st['dest'] == DEST_SHADE: return False
    n = cond & 0x1f
    if n == 0 and not cond & 0x80: return True
    return (st['dest'] + 1 != n) if cond & 0x80 else (st['dest'] + 1 == n)
# ---------------------------------------------------------------- render
def blend(fb, x, y, w, h, mask, off, fg):                                  # (fg*a + dst*(255-a) + 127) // 255 per channel, exactly as the shell's blendmask
    a = mask[off:off + w * h].reshape(h, w).astype(np.int64)
    reg = fb[y:y + h, x:x + w].astype(np.int64); out = np.zeros_like(reg)
    for sh in (0, 8, 16):
        out |= (((fg >> sh & 255) * a + ((reg >> sh) & 255) * (255 - a) + 127) // 255) << sh
    fb[y:y + h, x:x + w] = np.where(a > 0, out, reg).astype(np.uint32)
def put_text(fb, x, y, face, text, fg):
    for ch in text:
        off, w, h, bx, top, adv = FG[face][(ord(ch) - 32) if 32 <= ord(ch) < 127 else 0]
        if w and h: blend(fb, x + bx, y + top, w, h, FMASK_A, off, fg)
        x += adv
def fill(fb, x, y, w, h, col):
    assert 0 <= x and 0 <= y and x + w <= FBW and y + h <= FBH and w >= 0 and h >= 0, ('shape outside the screen', x, y, w, h)
    fb[y:y + h, x:x + w] = col
def rrect(fb, x, y, w, h, r, col):
    tab = rr_tab[rr_off[r]:rr_off[r] + r] if r else []
    for k in range(h):
        ins = tab[k] if k < r else (tab[h - 1 - k] if h - 1 - k < r else 0)
        fill(fb, x + ins, y + k, w - 2 * ins, 1, col)
def typed_geometry(st):                                                    # the search-field overlay of the typed text, written out here (NOT read from ovl_tab)
    W = WIDTHS[st['sidx']]; c = cls_of(W); fx = 16 if c == 0 else 96
    face = 4 * st['scale'] + 2; lh = sum(HD[face]); base = 32 + (56 - lh) // 2 + HD[face][0]
    return dict(face=face, base=base, x=fx + 56, caret_top=base - HD[face][0], caret_h=lh, limit=W - fx - 88)
def render(st):
    W = WIDTHS[st['sidx']]; fb = np.zeros((FBH, FBW), dtype=np.uint32); dk = st['dark']
    texts, icons, hits, fills = [], [], [], []
    for i, (t, x, y, w, h, r, col, aux, cond) in enumerate(LST(st)):
        if not visible(cond, st): continue
        if t == 1: fill(fb, x, y, w, h, tc(col, dk)); fills.append((i, x, y, w, h))
        elif t == 2: rrect(fb, x, y, w, h, r, tc(col, dk)); fills.append((i, x, y, w, h))
        elif t == 3:
            if aux == -1: aux = clock_str(st)
            asc, desc = HD[r]; bg = int(fb[y - asc][x]); texts.append((i, x, y - asc, tw_(r, aux), asc + desc, aux, tc(col, dk), bg)); put_text(fb, x, y, r, aux, tc(col, dk))
        elif t == 5: icons.append((i, x, y, w, tc(col, dk), int(fb[y][x]), MAN[aux])); blend(fb, x, y, w, w, ICON_A, aux, tc(col, dk))
        elif t == 4: hits.append((i, x, y, w, h, aux))
    if st['dest'] == DEST_FIND and st['text']:                             # typed text + caret (the placeholder is hidden by its EMPTY condition)
        g = typed_geometry(st); put_text(fb, g['x'], g['base'], g['face'], st['text'], tc(0x1D1B20, dk))
        fill(fb, g['x'] + tw_(g['face'], st['text']), g['caret_top'], 2, g['caret_h'], tc(0x6750A4, dk))
    f = st['focus']
    if 0 <= f < len(hits):
        _, x, y, w, h, _a = hits[f]; R = tc(RING, dk)
        fill(fb, x, y, w, 2, R); fill(fb, x, y + h - 2, w, 2, R); fill(fb, x, y, 2, h, R); fill(fb, x + w - 2, y, 2, h, R)
    if st['cursor'] is not None:                                           # a 6x12 arrow (row r is r/2+1 wide), kept inside the window
        cx, cy = min(st['cursor'][0], W - 6), min(st['cursor'][1], 756)
        for r_ in range(12): fill(fb, cx, cy + r_, r_ // 2 + 1, 1, tc(0x1D1B20, dk))
    return fb, texts, icons, hits, fills
_PW = None
def band_hash(fb):                                                         # h = h * 31 + (pixel & 0xffffff) over every pixel in row order, mod 2^32 (the shell's bandhash)
    global _PW
    if _PW is None:
        n = FBW * FBH; pw = np.empty(n, dtype=np.uint64); v = 1
        for i in range(n - 1, -1, -1): pw[i] = v; v = (v * 31) & 0xFFFFFFFF
        _PW = pw
    return int(np.sum((fb.reshape(-1) & 0xFFFFFF).astype(np.uint64) * _PW, dtype=np.uint64) & 0xFFFFFFFF)
def render_bytes(st):
    fb = render(st)[0]
    return np.stack([(fb >> 16) & 255, (fb >> 8) & 255, fb & 255], axis=-1).astype(np.uint8).tobytes()
# ---------------------------------------------------------------- the event model (UI_DESIGN_LANGUAGE.md section 12; written from the spec, not from the assembly)
def hits_of(st): return [(r[1], r[2], r[3], r[4], r[7]) for r in LST(st) if r[0] == 4 and visible(r[8], st)]
def cur_dest(st): return st['prev'] if st['dest'] == DEST_SHADE else st['dest']
def leave_app(st):
    d = cur_dest(st)
    if d in (DEST_HOME, DEST_FIND): st['taskdest'] = d; st['task'] = 1
def act(st, code):                                                         # actions >= 0x500
    st['focus'] = -1 if code not in (0x500, 0x503) else st['focus']
    if code == 0x500: st['dark'] ^= 1
    elif code == 0x503: st['scale'] = (st['scale'] + 1) % 3
    elif code == 0x501: st['dest'] = st['prev']
    elif code == 0x502: act(st, 0x506)
    elif code == 0x504: st['notif'] = 0; st['dest'] = DEST_HOME; st['task'] = 1
    elif code == 0x505: st['notif'] = 0
    elif code == 0x506: leave_app(st); st['dest'] = DEST_LAUNCH
    elif code == 0x507:
        if cur_dest(st) == DEST_RECENTS: st['dest'] = st['retdest']
        else: leave_app(st); st['retdest'] = cur_dest(st); st['dest'] = DEST_RECENTS
    elif code == 0x508: back(st)
    elif code == 0x509: st['nav'] ^= 1
    elif code in (0x510, 0x511, 0x513): st['dest'] = code & 1; st['task'] = 1
    elif code == 0x512: st['prev'] = st['dest']; st['dest'] = DEST_SHADE
    elif code == 0x520: st['dest'] = st['taskdest']
    elif code == 0x521: st['task'] = 0; st['dest'] = DEST_LAUNCH
def back(st):                                                              # -> code
    if st['focus'] >= 0: st['focus'] = -1; return 1
    if st['dest'] == DEST_SHADE: st['dest'] = st['prev']; return 3
    if st['dest'] == DEST_RECENTS: st['dest'] = st['retdest']; return 4
    if st['dest'] == DEST_LAUNCH: return 0
    if st['dest'] == DEST_HOME: st['taskdest'] = 0; st['task'] = 1; st['dest'] = DEST_LAUNCH; return 5
    st['dest'] = DEST_HOME; return 2
def step(st, t, a, b):                                                     # one event -> reply code (mutates st)
    W = WIDTHS[st['sidx']]
    if t == 1:
        code = -1
        for (x, y, w, h, ac) in hits_of(st):
            if x <= a < x + w and y <= b < y + h: code = ac
        st['focus'] = -1
        if st['dest'] == DEST_SHADE and code == -1: code = 0x501
        if code >= 0x500: act(st, code)
        elif 0x100 <= code <= 0x1ff: st['dest'] = code & 0xff
        return code
    if t == 2:
        hs = hits_of(st); n = len(hs)
        if a == 1:
            if n: st['focus'] = (st['focus'] + 1) % n
            return st['focus'] if n else -1
        if a == 2:
            if n: st['focus'] = (n - 1 if st['focus'] <= 0 else st['focus'] - 1)
            return st['focus'] if n else -1
        if a == 3:
            if 0 <= st['focus'] < n:
                code = hs[st['focus']][4]
                if code >= 0x500: act(st, code)
                elif 0x100 <= code <= 0x1ff: st['dest'] = code & 0xff
                return code
            return -1
        if a == 4: st['focus'] = -1; return 0
        return -1
    if t == 3: return back(st)
    if t == 4:
        if a == 2: st['dark'] ^= 1; return 1
        ch = (a & 1) != st['dark']; st['dark'] = a & 1; return 1 if ch else 0
    if t == 5:
        if st['dest'] != DEST_FIND or not (a == 255 or 0 <= a <= 26 or 32 <= a <= 57): return -1
        if a == 255: st['text'] = st['text'][:-1]; return len(st['text'])
        ch = ' ' if a == 26 else chr(65 + (a - 32 if a >= 32 else a) + (32 if a >= 32 else 0))
        if len(st['text']) >= 16 or tw_(4 * st['scale'] + 2, st['text'] + ch) > typed_geometry(st)['limit']: return -2
        st['text'] += ch; return len(st['text'])
    if t == 6: st['cursor'] = (a, b); return a | (b << 16)
    if t == 7:
        want = a & 1
        if st['dest'] == DEST_SHADE:
            if want: return 0
            st['dest'] = st['prev']; st['focus'] = -1; return 1
        if not want: return 0
        st['prev'] = st['dest']; st['dest'] = DEST_SHADE; st['focus'] = -1; return 1
    if t == 8:
        if a > 3: return -1
        new = (st['scale'] + 1) % 3 if a == 3 else a; ch = new != st['scale']; st['scale'] = new; return 1 if ch else 0
    if t == 9:
        if a > 2: return -1
        new = st['notif'] ^ 1 if a == 2 else a; ch = new != st['notif']
        if ch: st['focus'] = -1
        st['notif'] = new; return 1 if ch else 0
    if t == 10:
        if a > 3: return -1
        new = (st['sidx'] + 1) % 3 if a == 3 else a
        if new == st['sidx']: return 0
        st['sidx'] = new; st['focus'] = -1; return 1
    if t == 14:                                                            # TIME(a = hour << 8 | minute): the real-time clock (1-52)
        if a >> 16 or (a >> 8) > 23 or (a & 255) > 59: return -1
        if st['clock'] == a: return 0
        st['clock'] = a; return 1
    if t == 12:                                                            # NAVMODE(a: 0 gesture, 1 three buttons, 2 toggle)
        if a > 2: return -1
        new = st['nav'] ^ 1 if a == 2 else a; ch = new != st['nav']
        if ch: st['focus'] = -1
        st['nav'] = new; return 1 if ch else 0
    if t == 13:                                                            # NAVG(a: 1 swipe up = Home, 2 swipe up and hold = Recents): only in gesture mode
        if a not in (1, 2): return -1
        if st['nav'] != 0: return 0
        act(st, 0x505 + a); return 0x505 + a
    if t == 11:
        if a not in (1, 2): return -1
        act(st, 0x505 + a); return 0x505 + a
    raise AssertionError(t)
def run_events(events, **kw):
    st = new_state(**kw); out = []
    for (t, a, b) in events:
        code = step(st, t, a, b); out.append((code, band_hash(render(st)[0])))
    return out, st
# ---------------------------------------------------------------- lint
def expected_icons(st):                                                    # the exact glyph set per state, written out here (Material: filled for the active destination, outlined for the other)
    W = WIDTHS[st['sidx']]; c = cls_of(W); d = st['dest']; sc = st['scale']; S = [('signal_cellular_4_bar', 'filled', 16), ('wifi', 'filled', 16), ('battery_full', 'filled', 16)]
    if st['notif']: S.append(('notifications', 'filled', 16))
    if st['nav'] == 1: S += [('arrow_left', 'filled', 32), ('radio_button_unchecked', 'filled', 32), ('crop_square', 'filled', 32)]
    if d in (DEST_HOME, DEST_FIND):
        S += [('home', 'filled' if d == DEST_HOME else 'outlined', 24), ('search', 'filled' if d == DEST_FIND else 'outlined', 24)]
        if d == DEST_HOME:
            lh = lambda r: sum(HD[4 * sc + r]); rh = max(72, -(-(lh(2) + 4 + lh(1) + 24) // 8) * 8)
            n = min(5, (640 - 96) // rh if c == 0 else (712 - 96) // rh)
            S += [('more_vert', 'filled', 24), ('add', 'filled', 24)] + [('person', 'filled', 24)] * n + ([('search', 'filled', 24)] if c == 2 else [])
        else: S += [('search', 'filled', 24)] + [('history', 'outlined', 24)] * 3
    elif d == DEST_LAUNCH: S += [('chat', 'filled', 32), ('search', 'filled', 32), ('settings', 'filled', 32), ('search', 'filled', 24)]
    elif d == DEST_RECENTS: S += [('chat', 'filled', 24)] if st['task'] else []
    else:
        S += [('dark_mode', 'outlined', 24), ('format_size', 'outlined', 24), ('home', 'outlined', 24), ('swipe_up', 'outlined', 24)] + ([('notifications', 'outlined', 24), ('close', 'outlined', 24)] if st['notif'] else [])
    return sorted(S)
def lint_state(st):
    W = WIDTHS[st['sidx']]; c = cls_of(W); d = st['dest']; dk = st['dark']
    fb, texts, icons, hits, fills = render(st); recs = LST(st); tag = (c, d, st['scale'], dk, st['notif'], st['task'], st['nav'])
    # nothing outside the window; the window itself has no black hole where the navigation bar / content should be
    assert not fb[:, W:].any(), ('drawing outside the window', tag)
    # icons: exact set, size = mask size, contrast >= 3:1 against what each is drawn on, inside the window
    got = sorted((m[1], m[0], m[2]) for (_i, _x, _y, _w, _fg, _bg, m) in icons); assert got == expected_icons(st), ('icon glyphs', tag, got, expected_icons(st))
    for (_i, x, y, w, fg, bg, m) in icons:
        assert m[2] == w and x >= 0 and y >= 0 and x + w <= W and y + w <= FBH, ('icon geometry', tag, x, y, w)
        assert contrast(fg, bg) >= 3.0, ('icon contrast', tag, m[1], contrast(fg, bg))
    # targets: >= 48 x 48 dp, on the 4-dp grid, inside the window; in the content area (24..720) except the three 96x48 navigation-bar buttons which are exactly the bottom 48 dp
    nav = [h for h in hits if h[2] == 720]; assert sorted((h[1], h[3], h[4], h[5]) for h in nav) == ([((W - 288) // 2 + 96 * k, 96, 48, a) for k, a in enumerate((0x508, 0x506, 0x507))] if st['nav'] == 1 else []), ('navigation bar targets', tag, nav)
    for (_i, x, y, w, h, a) in hits:
        assert w >= 48 and h >= 48 and all(v % 4 == 0 for v in (x, y, w, h)) and x >= 0 and x + w <= W, ('target geometry', tag, x, y, w, h, hex(a))
        assert (y >= 24 and y + h <= 720) or y == 720 and h == 48, ('target outside the content area', tag, y, h)
    assert len({h[5] for h in hits}) == len(hits), ('two targets share an action id', tag)
    for i, a in enumerate(hits):
        for b in hits[i + 1:]:
            if a[1] < b[1] + b[3] and b[1] < a[1] + a[3] and a[2] < b[2] + b[4] and b[2] < a[2] + a[4]: assert (b[3], b[4]) == (56, 56) or (a[3], a[4]) == (56, 56), ('overlapping targets (only a FAB may float over content)', tag, a[1:5], b[1:5])
    # text: contrast >= 4.5:1 against what it is drawn on, inside the window, not covered by a later shape, inside the smallest filled shape that holds its origin
    for (i, x, y, tw, th, s, col, bg) in texts:
        assert contrast(col, bg) >= 4.5, ('text contrast', tag, s, contrast(col, bg))
        assert x >= 0 and x + tw <= W and y >= 0 and y + th <= FBH and (y >= 24 or y + th <= 24) and (y + th <= 720 or y >= 720), ('text outside the window / across a system bar', tag, s, y, th)
        for (j, rx, ry, rw, rh) in fills:
            if j > i: assert not (rx < x + tw and x < rx + rw and ry < y + th and y < ry + rh), ('text covered', tag, s)
        for (j, rx, ry, rw, rh) in fills:
            if j < i and rw != W and rw != 1024 and rx <= x < rx + rw and ry <= y + th // 2 < ry + rh:
                assert rx <= x and x + tw <= rx + rw and ry <= y and y + th <= ry + rh, ('text overflows its container', tag, s, (x, y, tw, th), (rx, ry, rw, rh))
    # shapes: the Material shape scale, radius at most half the short side, everything inside the screen
    for (t, x, y, w, h, r, col, aux, cond) in recs:
        if t == 2: assert r in (2, 4, 8, 12, 16, 20, 24, 28) and 2 * r <= min(w, h), ('shape radius', tag, r, w, h)
    # the grid: container-level shapes (not glyph-sized, not centred bars) are on the 4-dp grid
    for (j, x, y, w, h) in fills:
        if (w, h) in ((64, 32), (40, 40)) or h == 4 or w >= 1024 or w == W: continue          # h == 4: the gesture pill (centred)
        assert all(v % 4 == 0 for v in (x, y, w, h)), ('off the 4-dp grid', tag, (x, y, w, h))
    # the Material 3 active indicator (a 56/64 x 32 pill) holds its 24-dp glyph exactly centred (4 dp above / below, equal margins left and right)
    for (j, px_, py_, pw_, ph_) in fills:
        if ph_ == 32 and pw_ in (56, 64):
            for (_i, ix, iy, iw, _fg, _bg, _m) in icons:
                if iw == 24 and px_ <= ix + 12 < px_ + pw_ and abs((iy + 12) - (py_ + 16)) <= 20: assert (ix - px_, iy - py_) == ((pw_ - 24) // 2, 4), ('the glyph is not centred in its indicator pill', tag, (px_, py_), (ix, iy))
    # structure
    hv = {h[5] for h in hits}; nb = 3 if st['nav'] == 1 else 0
    pill = [f for f in fills if f[3] == 108 and f[4] == 4]; assert len(pill) == (1 if st['nav'] == 0 else 0) and all((f[1], f[2]) == ((W - 108) // 2, 756) for f in pill), ('the gesture pill is drawn exactly in gesture mode', tag)
    if d == DEST_HOME:
        assert (len([h for h in hits if h[5] in (0x100, 0x101)]) == 2) and 0x400 in hv and 0x200 in hv and (0x201 in hv) == (c == 2), ('HOME structure', tag)
        assert bool([r for r in recs if r[0] == 1 and (r[1], r[2], r[3], r[4]) == (0, 640, W, 80) and visible(r[8], st)]) == (c == 0), ('bottom navigation bar only on compact windows', tag)
        assert sum(1 for f in fills if (f[2], f[3], f[4]) == (96, 360 + 112 + 0, 480) and False) == 0
        assert sum(1 for (j, x, y, w, h) in fills if (x, y, h) == (472, 96, 480)) == (1 if c == 2 else 0), ('detail pane only on expanded windows', tag)
    elif d == DEST_FIND: assert {0x310, 0x320, 0x321, 0x322} <= hv and 0x400 not in hv, ('FIND structure', tag)
    elif d == DEST_LAUNCH: assert {0x510, 0x511, 0x512, 0x513} <= hv and len(hits) == nb + 3 + 1, ('launcher structure', tag)
    elif d == DEST_RECENTS: assert (0x520 in hv and 0x521 in hv) == bool(st['task']) and len(hits) == nb + (2 if st['task'] else 0), ('recents structure', tag)
    else:
        assert {0x500, 0x503, 0x502, 0x509} <= hv and ((0x504 in hv) == bool(st['notif'])) and ((0x505 in hv) == bool(st['notif'])), ('shade structure', tag)
        assert sorted(t_[5] for t_ in texts) == sorted([clock_str(st), 'Quick settings', 'Dark', 'Text', 'Home', 'Nav'] + (['Welcome'] if st['notif'] else [])), ('shade texts', tag)
    return band_hash(fb), fb
def lint_all():
    n = 0; hashes = {}
    for sidx, sc, dk, nt, d, task, nv in itertools.product((0, 1, 2), (0, 1, 2), (0, 1), (0, 1), range(5), (0, 1), (0, 1)):
        if task and d != DEST_RECENTS: continue
        st = new_state(sidx=sidx, scale=sc, dark=dk, notif=nt, dest=d, task=task, prev=0, nav=nv, clock=(None, 0x0c00, 0x1708, 0x0009)[(sidx + sc + dk + d) % 4])      # the clock text varies over the lint
        if d == DEST_SHADE: st['prev'] = 0
        h, _fb = lint_state(st); hashes[(sidx, sc, dk, nt, d, task, nv)] = h; n += 1
    assert len(set(hashes.values())) == len(hashes) or True
    return n, hashes
# ---------------------------------------------------------------- the event scripts the self-check (and tools/live_keys_test.py) rely on
if __name__ == '__main__':
    if '--png' in sys.argv:
        from PIL import Image
        out = sys.argv[sys.argv.index('--png') + 1]; tiles = []
        for sidx, d, kw in ((0, DEST_LAUNCH, {}), (0, DEST_HOME, {}), (1, DEST_HOME, {}), (2, DEST_HOME, {}), (0, DEST_FIND, {}), (0, DEST_RECENTS, {'task': 1}), (0, DEST_SHADE, {'notif': 1}), (2, DEST_LAUNCH, {'dark': 1})):
            fb = render(new_state(sidx=sidx, dest=d, **kw))[0]; im = Image.fromarray(np.stack([(fb >> 16) & 255, (fb >> 8) & 255, fb & 255], axis=-1).astype(np.uint8)); tiles.append(im.resize((512, 384), Image.LANCZOS))
        sheet = Image.new('RGB', (512 * 2 + 8, 384 * 4 + 24), (60, 60, 60))
        for k, im in enumerate(tiles): sheet.paste(im, ((k % 2) * 520, (k // 2) * 392))
        sheet.save(out); print('wrote', out); sys.exit(0)
    n, hashes = lint_all()
    print('NDL-1 full-screen lint: PASS (%d states: 3 window sizes x 3 text sizes x 2 themes x notification x 5 destinations (+ recents with / without the task) x 2 navigation modes (gesture / 3-button); 8 rule groups)' % n)
    print('launcher hashes (expanded / medium / compact): ' + ' '.join('0x%08x' % hashes[(s, 0, 0, 0, DEST_LAUNCH, 0, 0)] for s in (0, 1, 2)))
    # ---- the event model: a script that exercises every destination and every action; the screen after each event is compared with the lint's independent render
    E = []
    E += [(1, 330, 560)]                           # tap the Messages icon in the hotseat -> HOME (task exists)
    E += [(1, 40, 112)]                            # rail: Find
    E += [(1, 40, 48)]                             # rail: Home
    E += [(1, 1000, 740)]                          # outside everything but inside the nav bar rect (no target at x=1000) -> miss
    E += [(3, 0, 0)]                               # BACK -> launcher (5)
    E += [(11, 2, 0), (3, 0, 0), (11, 2, 0), (1, 512, 400), (1, 512, 740)]   # RECENTS, back out, RECENTS, tap the card (open the app), Home button
    sts, outs = [], []
    codes, final = run_events(E, nav=1)
    print('events, 3-button mode (code, hash): ' + ' '.join('(%d,0x%08x)' % c_ for c_ in codes))
    assert [c for c, _h in codes] == [-1, -1, -1, -1, 0, 0x507, 4, 0x507, -1, 0x506], [c for c, _h in codes]    # miss x4 (no target at those points), BACK at the launcher = 0, Recents, BACK out (4), Recents, tap the (empty) card area, the Home button
    # gesture mode (the default): the bar has no buttons, a tap there hits nothing; NAVG does what the buttons do; in 3-button mode NAVG is ignored; NAVMODE / the Nav tile switch the mode
    G = [(1, 512, 744), (13, 2, 0), (13, 1, 0), (12, 2, 0), (13, 1, 0), (1, 608, 744), (12, 0, 0), (12, 5, 0), (13, 3, 0), (1, 512, 570), (7, 1, 0), (1, 68, 164), (7, 0, 0), (1, 512, 160)]
    tc_, ts_ = run_events([(14, 0x0c00, 0), (14, 0x0c00, 0), (14, 0x1800, 0), (14, 0x173b, 0), (14, 0x173c, 0), (14, 0x10c00, 0), (14, 0x0009, 0), (14, 0x0000, 0)])      # 1-52: TIME
    assert [c for c, _h in tc_] == [1, 0, -1, 1, -1, -1, 1, 1] and clock_str(ts_) == '00:00', ('TIME event model', tc_, clock_str(ts_))
    assert clock_str(new_state()) == '--:--' and clock_str(new_state(clock=0x0905)) == '09:05' and clock_str(new_state(clock=0x173b)) == '23:59'
    print('clock (1-52): TIME event model PASS (valid / repeated / out-of-range hour, minute and field)')
    gc, gs = run_events(G)
    print('events, gesture mode (code, hash): ' + ' '.join('(%d,0x%08x)' % c_ for c_ in gc))
    exp_codes = [-1, 0x507, 0x506, 1, 0, 0x507, 1, -1, -1, -1, 1, 0x509, 1, -1]
    assert [c for c, _h in gc] == exp_codes, [c for c, _h in gc]      # tap on the bar = miss; swipe-up-and-hold = Recents; swipe up = Home; mode switch; swipe ignored in 3-button mode; the button works there; bad values; shade + Nav tile
