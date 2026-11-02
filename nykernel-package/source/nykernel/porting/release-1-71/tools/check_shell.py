#!/usr/bin/env python3
"""Independent lint + reference renderer for the NDL-1 shell (userprog/shell_layout.inc, userprog/prog_shell.s). Shares no code with make_shell_layout.py: it PARSES the
generated assembly, re-renders all three window classes from the records in pure Python, applies the design rules of UI_DESIGN_LANGUAGE.md as assertions, and prints
the band hashes the kernel's boot test must reproduce. Usage: check_shell.py [--png out.png] [--print-hashes]"""
import os, re, sys, math
src = open('userprog/shell_layout.inc').read()
asm = open('userprog/prog_shell.s').read()
FBW, ROWS = 1024, 192
# ---- parse ----
lists = {}; cur = None
for ln in src.splitlines():
    m = re.match(r'dl_c(\d+):', ln)
    if m: cur = int(m.group(1)); lists[cur] = []; continue
    if re.match(r'\w+:', ln): cur = None                                       # 1-51: any other label (the full-screen lists dl_t*, ovl_tab, ...) ends the band list
    m = re.match(r'\s+\.long (.*)$', ln)
    if m and cur is not None and 'rr_' not in ln:
        f = [x.strip() for x in m.group(1).split(',')]
        if len(f) == 8: lists[cur].append([int(v, 0) if '-' not in v else v for v in f])
    if ln.startswith('rr_off:'): cur = None
strs = {m.group(1): m.group(2) for m in re.finditer(r'^(s_\w+): \.asciz "([^"]*)"', src, re.M)}
rr_off = [int(v) for v in re.search(r'rr_off:\n\s+\.long (.*)', src).group(1).split(',')]
rr_tab = [int(v) for v in re.search(r'rr_tab:\n\s+\.byte (.*)', src).group(1).split(',')]
ICONS = bytes(int(v) for ln in re.search(r'icons:[^\n]*\n((?:\s+\.byte [^\n]*\n)+)', src).group(1).splitlines() for v in ln.split('.byte')[1].split(','))   # alpha masks of the Material icons (1-45)
MAN = {int(m.group(5)): (m.group(1), m.group(2), int(m.group(3)), int(m.group(4))) for m in re.finditer(r'/\* icon (\w+) (\w+) (\d+) (\d+) @ (\d+) \*/', src)}   # manifest: offset -> (variant, name, size, rotation)
_hdr = re.search(r'font_hdr:\n((?:\s+\.long [^\n]*\n){12})', src).group(1).splitlines(); HD = [tuple(int(v) for v in ln.split('.long')[1].split(',')) for ln in _hdr]          # (ascent, descent) per face
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
SCALE = [0]                                                                  # font scale of the state being rendered: 0 / 1 / 2 = 100% / 130% / 200%
def LS(c): return lists[c + 3 * SCALE[0]]
def tw_(face, t): return sum(FG[face][(ord(c) - 32) if 32 <= ord(c) < 127 else 0][5] for c in t)    # pen advance of a string
assert sorted(lists) == list(range(9))      # list index = 3 * font scale + width class
for c in lists:
    for rec in lists[c]:
        if isinstance(rec[7], str):
            assert rec[7].endswith(' - shell_base'); rec[7] = strs[rec[7].split(' - ')[0]]
    assert lists[c][-1][0] == 0
    lists[c].pop()
    for rec in lists[c]: rec[8:] = [rec[0] >> 8]; rec[0] &= 0xff   # rec = [type, x, y, w, h, r, col, aux, cond]
# ---- the corner table is a circle (not just "whatever the generator wrote") ----
radii = sorted({r for r in range(1, 29) if rr_off[r] != 0 or r == 2})
for r in radii:
    t = rr_tab[rr_off[r]:rr_off[r] + r]
    assert len(t) == r and t[-1] == 0 and all(a >= b for a, b in zip(t, t[1:])), ('corner table not monotone', r)
    for k, ins in enumerate(t):
        cy = r - k - 0.5
        for x, want_in in ((ins + 0.5, True), (ins - 0.5, False)):
            d = math.hypot(r - x, cy)
            assert (d <= r + 0.5) if want_in else (d > r - 0.5 or ins == 0), ('corner not circular', r, k, ins)
# ---- font (same source of truth as check_gfx.py) ----
GL = {}; g = None
for ln in open('tools/font5x7.txt').read().splitlines():
    if ln.startswith(';') or not ln.strip(): continue
    if ln.startswith('glyph '): g = ' ' if ln[6:] == 'SPACE' else ln[6:]; GL[g] = []
    else: GL[g].append(ln)
def lum(c):
    def ch(v): v /= 255; return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c >> 16 & 255) + 0.7152 * ch(c >> 8 & 255) + 0.0722 * ch(c & 255)
def contrast(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True); return (la + 0.05) / (lb + 0.05)
NOTIF = [0]                                                                  # a notification is posted (1-48)
def visible(cond, active):
    if cond & 0x20 and not NOTIF[0]: return False              # NOTIF: only while a notification is posted
    cond &= 0xdf
    if cond & 0x40 and active == 2: return False               # HIDE: yields to the quick-settings shade (destination 2)
    cond &= 0xbf
    if cond == 0: return True
    return (active + 1 != (cond & 0x7f)) if cond & 0x80 else (active + 1 == cond)
RING = 0x6750A4; SURFACE_REF = 0xFEF7FF
# Material 3 baseline DARK colour roles (written out here from UI_DESIGN_LANGUAGE.md section 7, NOT read from the generator)
DARK = {0xFEF7FF: 0x141218, 0xF3EDF7: 0x211F26, 0xEADDFF: 0x4F378B, 0xE8DEF8: 0x4A4458, 0x1D1B20: 0xE6E0E9, 0x49454F: 0xCAC4D0, 0x4F378B: 0xEADDFF, 0x6750A4: 0xD0BCFF}
THEME = [0]
def tc(col): return DARK.get(col, col) if THEME[0] else col
ICON_LOG = []
def blend(fb, x, y, w, h, off, mask, fg):            # (fg*a + dst*(255-a) + 127) // 255 per channel, exactly as the shell's blendmask
    for dy in range(h):
        for dx in range(w):
            a = mask[off + dy * w + dx]
            if a:
                d = fb[y + dy][x + dx]; fb[y + dy][x + dx] = sum((((fg >> sh & 255) * a + (d >> sh & 255) * (255 - a) + 127) // 255) << sh for sh in (0, 8, 16))
def put_text(fb, x, y, face, text, fg):
    for ch in text:
        off, w, h, bx, top, adv = FG[face][(ord(ch) - 32) if 32 <= ord(ch) < 127 else 0]
        if w and h: blend(fb, x + bx, y + top, w, h, off, FMASK, fg)
        x += adv
def render(c, W, active=0, focus=-1, text='', cursor=None):
    fb = [[0] * FBW for _ in range(ROWS)]; texts = []
    recs = LS(c)
    def fillrect(x, y, w, h, col):
        for yy in range(y, y + h):
            for xx in range(x, x + w): fb[yy][xx] = col
    vis_hits = []; ICON_LOG[:] = []
    for i, (t, x, y, w, h, r, col, aux, cond) in enumerate(recs):
        if not visible(cond, active): continue
        if t == 1: fillrect(x, y, w, h, tc(col))
        elif t == 2:
            tab = rr_tab[rr_off[r]:rr_off[r] + r] if r else []
            for k in range(h):
                ins = tab[k] if k < r else (tab[h - 1 - k] if h - 1 - k < r else 0)
                fillrect(x + ins, y + k, w - 2 * ins, 1, tc(col))
        elif t == 3:                                  # 1-46: Roboto, proportional; y = BASELINE, r = face
            asc, desc = HD[r]; tw = tw_(r, aux); bg = fb[y - asc][x]
            texts.append((i, x, y - asc, tw, asc + desc, aux, tc(col), bg))
            put_text(fb, x, y, r, aux, tc(col))
        elif t == 5:                                  # 1-45: a Material icon: size x size alpha mask at ICONS[aux], blended: (fg*a + dst*(255-a) + 127) // 255 per channel
            fg = tc(col); ICON_LOG.append((x, y, w, fg, fb[y][x])); blend(fb, x, y, w, w, aux, ICONS, fg)
        elif t == 4: vis_hits.append((i, x, y, w, h, aux))
    if active == 1 and text:                      # 1-38/39/46: the search field overlay (placeholder hidden), the typed text (Roboto body-large) and a caret
        bw = W - 96 - 16; tab = rr_tab[rr_off[28]:rr_off[28] + 28]
        for k in range(56):
            ins = tab[k] if k < 28 else (tab[55 - k] if 55 - k < 28 else 0)
            fillrect(96 + ins, 96 + k, bw - 2 * ins, 1, tc(0xF3EDF7))
        bf_ = 4 * SCALE[0] + 2; lh_ = sum(HD[bf_]); bl_ = 96 + (56 - lh_) // 2 + HD[bf_][0]       # the typed text: body-large of this font scale, centred in the 56-dp field (written here, not read from ovl_tab)
        put_text(fb, 120, bl_, bf_, text, tc(0x1D1B20))
        cw_ = tw_(bf_, text); fillrect(120 + cw_, bl_ - HD[bf_][0], 2, lh_, tc(0x6750A4))   # caret in the primary colour (Material), so it cannot be read as a letter
        assert contrast(tc(0x1D1B20), tc(0xF3EDF7)) >= 4.5 and contrast(tc(0x6750A4), tc(0xF3EDF7)) >= 3.0 and cw_ <= W - 148, 'typed text must fit its field'
    if 0 <= focus < len(vis_hits):
        _, x, y, w, h, _a = vis_hits[focus]
        R_ = tc(RING); fillrect(x, y, w, 2, R_); fillrect(x, y + h - 2, w, 2, R_); fillrect(x, y, 2, h, R_); fillrect(x + w - 2, y, 2, h, R_)
    if cursor is not None:                          # 1-41: the pointer cursor, a 6x12 arrow (row r is r/2+1 wide) in on-surface, kept inside the window
        cx, cy = min(cursor[0], W - 6), min(cursor[1], 180)
        for r_ in range(12): fillrect(cx, cy + r_, r_ // 2 + 1, 1, tc(0x1D1B20))
    return fb, texts, vis_hits
def band_hash(fb):
    h = 0
    for row in fb:
        for p in row: h = (h * 31 + (p & 0xFFFFFF)) & 0xFFFFFFFF
    return h
hashes = {}; fbs = {}
import itertools
for nt_, sc_, dark, (c, W), active in itertools.product((0, 1), (0, 1, 2), (0, 1), ((0, 360), (1, 640), (2, 1024)), (0, 1, 2)):
    NOTIF[0] = nt_; THEME[0] = dark; SCALE[0] = sc_; K = (c, active) + (('d',) if dark else ()) + (('s%d' % sc_,) if sc_ else ()) + (('n',) if nt_ else ())
    fb, texts, hit = render(c, W, active); fbs[K] = fb; hashes[K] = band_hash(fb); recs = [r for r in LS(c) if visible(r[8], active)]
    cls = 0 if W < 600 else (1 if W < 840 else 2); assert cls == c
    vis = [r for r in recs if r[0] in (1, 2)]
    # 1-45 icons: the exact set per state (vanilla Material glyphs), inside the window, glyph contrast >= 3:1 against what each is drawn on
    card = bool(nt_ and active == 2 and W >= 600)                                  # 1-48: the notification card exists only in the shade of a window >= 600 dp
    want_icons = (6 if active == 2 else 3 + 2 + (2 if W >= 840 else 1) + (1 if (active == 0 and W < 840) else 0)) + nt_ + (2 if card else 0)
    assert len(ICON_LOG) == want_icons, ('icon count', c, active, len(ICON_LOG), want_icons)
    got = sorted((MAN[r[7]][1], MAN[r[7]][0]) for r in recs if r[0] == 5)                      # WHICH glyphs, written out here (not read from the generator): Material 3 = FILLED for the active destination, OUTLINED for inactive
    want_set = [('battery_full', 'filled'), ('signal_cellular_4_bar', 'filled'), ('wifi', 'filled')]
    if nt_: want_set += [('notifications', 'filled')]
    if card: want_set += [('notifications', 'outlined'), ('close', 'outlined')]
    if active == 2: want_set += [('dark_mode', 'outlined'), ('format_size', 'outlined'), ('home', 'outlined')]
    else:
        want_set += [('home', 'filled' if active == 0 else 'outlined'), ('search', 'filled' if active == 1 else 'outlined'), ('more_vert', 'filled')] + ([('search', 'filled')] if W >= 840 else []) + ([('add', 'filled')] if active == 0 and W < 840 else [])
    assert got == sorted(want_set), ('icon glyphs', c, active, got)
    assert all(MAN[r[7]][2] == r[3] for r in recs if r[0] == 5), 'icon record size must equal its mask size'
    for (ix, iy, isz, ifg, ibg) in ICON_LOG:
        assert isz in (16, 24) and ix >= 0 and ix + isz <= W and iy >= 0 and iy + isz <= 192, ('icon outside the window', c, ix, iy, isz)
        assert contrast(ifg, ibg) >= 3.0, ('icon contrast', c, ix, iy, contrast(ifg, ibg))
    # R1 targets >= 48 x 48 dp, 4-dp grid, outside the 24-dp system insets (status bar on top, gesture area at the bottom), inside the window
    for (i, x, y, w, h, aux) in hit:
        assert w >= 48 and h >= 48, ('target < 48dp', c, x, y, w, h)
        assert all(v % 4 == 0 for v in (x, y, w, h)), ('target off the 4-dp grid', c, x, y, w, h)
        assert y >= 24 and y + h <= 168, ('target inside a system inset', c, x, y, w, h)
        assert x >= 0 and x + w <= W, ('target outside the window', c)
    for i, a in enumerate(hit):
        for b in hit[i + 1:]:
            ov = a[1] < b[1] + b[3] and b[1] < a[1] + a[3] and a[2] < b[2] + b[4] and b[2] < a[2] + a[4]
            if ov: assert b[3] == 56 and b[4] == 56, ('overlapping targets (only a FAB may float over content)', c, a[1:5], b[1:5])
    assert len({h[5] for h in hit}) == len(hit), 'two targets share an action id'
    # R2 geometry on the 4-dp grid (status icons, the gesture pill and icon strokes are exempt)
    for (t, x, y, w, h, r, col, _a, _c) in vis:
        if y < 24 or (h == 4 and y >= 176) or (w == W and h == 192) or (w, h) in ((4, 24), (24, 4)): continue
        assert all(v % 4 == 0 for v in (x, y, w, h)), ('off the 4-dp grid', c, (t, x, y, w, h))
    # R3 contrast: text >= 4.5:1 against what it is drawn on; icon glyphs >= 3:1; the focus ring >= 3:1 against the surface
    for (i, x, y, tw, th, s_, col, bg) in texts:
        assert contrast(col, bg) >= 4.5, ('text contrast', c, s_, contrast(col, bg))
        assert x >= 0 and x + tw <= W and y >= 24 and y + th <= 168, ('text outside the safe area', c, s_)
        for (t, rx, ry, rw, rh, *_x) in [r for r in LS(c)[i + 1:] if visible(r[8], active)]:
            if t in (1, 2): assert not (rx < x + tw and x < rx + rw and ry < y + th and y < ry + rh), ('text covered', c, s_)
        # 1-47: every text line box lies INSIDE the smallest visible filled shape that holds its origin (a card, tile, pill, search bar, pane); the window itself and the app bar's bare surface are the only exceptions
        box = [(rx, ry, rw, rh) for (t2, rx, ry, rw, rh, *_x) in LS(c)[:i] if visible(LS(c)[LS(c).index(next(q for q in LS(c) if q[:2] == [t2, rx] and q[2] == ry and q[3] == rw))][8], active) and t2 in (1, 2) and (rw, rh) != (W, 192) and rx <= x < rx + rw and ry <= y + th // 2 < ry + rh]
        for (rx, ry, rw, rh) in box: assert rx <= x and x + tw <= rx + rw and ry <= y and y + th <= ry + rh, ('text overflows its container', c, sc_, s_, (x, y, tw, th), (rx, ry, rw, rh))
    for (t, x, y, w, h, r, col, _a, _c) in vis:
        if (w, h) in ((24, 24), (4, 24), (24, 4)) and y >= 24: assert contrast(tc(col), fb[y - 2][x - 2] if x > 1 and y > 1 else 0xFFFFFF) >= 3.0, ('glyph contrast', c, x, y)
    assert all(contrast(tc(RING), tc(bgc)) >= 3.0 for bgc in (SURFACE_REF, 0xF3EDF7, 0xE8DEF8)), 'focus ring contrast'
    # R4 shapes: Material shape scale only; radius <= half the short side
    for (t, x, y, w, h, r, col, _a, _c) in vis:
        assert r in (0, 2, 4, 8, 12, 16, 28), ('shape radius not on the scale', r); assert 2 * r <= min(w, h)
    # R5 structure per width class and destination
    if active == 2:      # 1-44 the quick-settings shade: no rail, no cards; exactly three 104x56 pill tiles (dark theme 0x500, text size 0x503, home 0x502; 1-47), the status icons and the pill are redrawn on top
        assert sorted((h[5], h[1], h[2], h[3], h[4]) for h in hit) == [(0x500, 16, 72, 104, 56), (0x502, 240, 72, 104, 56), (0x503, 128, 72, 104, 56)] + ([(0x504, 360, 72, W - 424, 56), (0x505, W - 64, 76, 48, 48)] if card else []), ('shade targets', hit)
        if card: assert sum(1 for r in vis if r[0] == 2 and r[5] == 12 and (r[1], r[2], r[3], r[4]) == (360, 72, W - 376, 56)) == 1, 'one notification card'
        assert sum(1 for r in vis if r[0] == 2 and r[5] == 28 and (r[3], r[4]) == (104, 56)) == 3, 'three tiles'
        pill = [r for r in vis if r[4] == 4 and r[2] == 180]; assert len(pill) == 1 and pill[0][1] == (W - 108) // 2 and pill[0][3] == 108
        assert any(s_ == 'Quick settings' for (_i, _x, _y, _tw, _th, s_, _c2, _b) in texts) and sorted(t_[5] for t_ in texts) == sorted(['Dark', 'Home', 'Quick settings', 'Text'] + (['Welcome'] if card else [])), 'the shade title, the three tile labels and (with a card) its one line are the only texts'
        assert all(all(p == 0 for p in row[W:]) for row in fb), ('drawing outside the window', c)
        continue
    rail = [h for h in hit if h[1] == 0]; assert len(rail) == 2 and all(h[3] == 80 for h in rail), 'navigation rail missing (compact height => rail)'
    cards = [r for r in vis if r[0] == 2 and r[5] == 12 and r[4] == 48]
    fab = [r for r in vis if r[0] == 2 and r[3] == 56 and r[4] == 56 and r[5] == 16 and r[2] >= 96]
    detail = [r for r in vis if r[0] == 2 and r[5] == 28 and r[4] == 80]
    search = [r for r in vis if r[0] == 2 and r[5] == 28 and r[4] == 56 and r[2] == 96]
    want = {(0, 0): (1, 1, 0, 0), (1, 0): (2, 1, 0, 0), (2, 0): (1, 0, 1, 0)}
    assert (len(cards), len(fab), len(detail), len(search)) == (want[(c, 0)] if active == 0 else (0, 0, 0, 1)), ('structure', c, active)
    for cd in cards: assert cd[1] >= 96 and cd[1] + cd[3] <= W - 16, ('card margin < 16 dp', c)
    pill = [r for r in vis if r[4] == 4 and r[2] == 180]; assert len(pill) == 1 and pill[0][1] == (W - 108) // 2 and pill[0][3] == 108
    # R6 nothing is drawn outside the window; R7 state is never colour alone: the active rail item has a pill indicator
    assert all(all(p == 0 for p in row[W:]) for row in fb), ('drawing outside the window', c)
    assert sum(1 for r in vis if r[0] == 2 and r[5] == 16 and r[3] == 56 and r[4] == 32) == 1, 'exactly one active indicator'
THEME[0] = 0; NOTIF[0] = 0; SCALE[0] = 0
print('NDL-1 lint: PASS (2 themes x 3 window classes x 2 destinations + the quick-settings shade x 7 rule groups; corner table circular for radii %s)' % radii)
print('hashes (class 0/1/2, 1024x192 band): ' + ' '.join('0x%08x' % hashes[(c, 0)] for c in (0, 1, 2)))
print('FIND-state hashes (class 0/1/2): ' + ' '.join('0x%08x' % hashes[(c, 1)] for c in (0, 1, 2)))
print('flat-zero band (the rejected run): 0x%08x' % band_hash([[0] * FBW for _ in range(ROWS)]))
for c, W in ((0, 360), (1, 640), (2, 1024)):
    fb = fbs[(c, 0)]; print('class %d W=%d samples: (2,2)=%06x (W-1,100)=%06x (W+4,100)=%s records visible (HOME)=%d' % (c, W, fb[2][2], fb[100][W - 1], ('%06x' % fb[100][W + 4]) if W + 4 < FBW else '-1', sum(1 for r in lists[c] if visible(r[8], 0))))
# ---- independent model of the interactive shell (pointer + keyboard) at W = 1024 (class 2); the kernel's boot test compares reply codes and band hashes event by event ----
def run_events(c, W, events):
    active, focus, out, dark, text, cur, prev, scale, notif = 0, -1, [], 0, '', None, 0, 0, 0
    clk = [-1]                                        # the last time the shell was told (1-52); -1 = none yet
    def act_(code):                                   # shade actions >= 0x500
        nonlocal active, focus, dark, scale, notif
        if code == 0x504: notif = 0; active = 0; focus = -1       # 1-48: open the notification: dismissed, shade closed, HOME
        elif code == 0x505: notif = 0; focus = -1                  # 1-48: dismiss it, the shade stays
        elif code == 0x500: dark ^= 1
        elif code == 0x503: scale = (scale + 1) % 3     # 1-47: the text-size tile: 100% -> 130% -> 200% -> 100%
        elif code == 0x501: active = prev; focus = -1
        elif code == 0x502: active = 0; focus = -1
    for (t, a, b) in events:
        SCALE[0] = scale; NOTIF[0] = notif
        hits = [(r[1], r[2], r[3], r[4], r[7]) for r in LS(c) if r[0] == 4 and visible(r[8], active)]
        if t == 1:
            code = -1
            for (x, y, w, h, act) in hits:
                if x <= a < x + w and y <= b < y + h: code = act
            focus = -1
            if active == 2 and code == -1: code = 0x501      # a tap outside the tiles closes the shade
            if code >= 0x500: act_(code)
            elif 0x100 <= code <= 0x1ff: active = code & 0xff
        elif t == 7:                                  # SHADE (a = 1 open / 0 close): 1 = changed, 0 = already so
            want = a & 1
            if active == 2:
                if want: code = 0
                else: active = prev; focus = -1; code = 1
            elif not want: code = 0
            else: prev = active; active = 2; focus = -1; code = 1
        elif t == 3:                                  # BACK: close the focus ring, else close the shade, else leave a destination for HOME, else not consumed
            if focus >= 0: focus = -1; code = 1
            elif active == 2: active = prev; code = 3
            elif active != 0: active = 0; code = 2
            else: code = 0
        elif t == 6:                                  # MOVE: the cursor appears / moves; reply = x | y << 16
            cur = (a, b); code = a | (b << 16)
        elif t == 5:                                  # TEXT: letters 0..25, 26 space, 255 backspace; only while FIND is active; 16 characters at most
            if active != 1 or not (a == 255 or 0 <= a <= 26 or 32 <= a <= 57): code = -1
            elif a == 255: text = text[:-1]; code = len(text)
            elif len(text) >= 16 or tw_(4 * scale + 2, text + (' ' if a == 26 else chr(65 + a))) > W - 148: code = -2      # full: 16 characters or wider than the field (W - 148 dp)
            else: text += ' ' if a == 26 else chr(65 + a); code = len(text)
        elif t == 14:                                 # TIME (a = hour << 8 | minute): 1 = changed, 0 = the same, -1 = not a valid time (1-52; the band layouts show no clock, only the reply is checked)
            if a >> 16 or (a >> 8) > 23 or (a & 255) > 59: code = -1
            else: code = 0 if a == clk[0] else 1; clk[0] = a
        elif t == 8:                                  # FONTSCALE (a: 0 / 1 / 2 set, 3 next): 1 = changed, 0 = already that, -1 = bad value (1-47)
            if a > 3: code = -1
            else: new = (scale + 1) % 3 if a == 3 else a; code = 1 if new != scale else 0; scale = new
        elif t == 9:                                  # POST (a: 1 post, 0 clear, 2 toggle): 1 = changed, 0 = already so, -1 = bad value; a change clears the focus ring (1-48)
            if a > 2: code = -1
            else: new = notif ^ 1 if a == 2 else a; code = 1 if new != notif else 0; focus = -1 if new != notif else focus; notif = new
        elif t == 4:                                  # THEME: 1 when it changed; a = 2 toggles (F2; the shade tile can change the theme too, so the router must not keep its own copy)
            if a == 2: dark ^= 1; code = 1
            else: code = 1 if (a & 1) != dark else 0; dark = a & 1
        elif t == 2:
            n = len(hits)
            if a == 1: focus = (focus + 1) % n if n else focus; code = focus if n else -1
            elif a == 2: focus = (n - 1 if focus <= 0 else focus - 1) if n else focus; code = focus if n else -1
            elif a == 3:
                code = -1
                if focus >= 0 and focus < n:
                    code = hits[focus][4]
                    if code >= 0x500: act_(code)
                    elif 0x100 <= code <= 0x1ff: active = code & 0xff
            elif a == 4: focus = -1; code = 0
            else: code = -1
        SCALE[0] = scale; NOTIF[0] = notif; THEME[0] = dark; fb, _t, _h = render(c, W, active, focus, text, cur); THEME[0] = 0; out.append((code, band_hash(fb)))
    SCALE[0] = 0; NOTIF[0] = 0
    return out
EVENTS = [(1, 40, 100), (1, 200, 120), (1, 1030, 100), (1, 2, 2)] + [(2, 1, 0)] * 6 + [(2, 2, 0)] * 4 + [(2, 3, 0), (2, 2, 0), (2, 3, 0), (1, 2, 2), (2, 3, 0), (2, 4, 0)]
EVENTS += [(4, 1, 0), (1, 40, 100), (2, 1, 0), (3, 0, 0), (3, 0, 0), (3, 0, 0), (4, 0, 0), (4, 0, 0)]   # 1-37: dark theme, go to FIND, focus ring, BACK x3 (ring, FIND->HOME, not consumed), light, light again
EVENTS += [(7, 1, 0), (7, 1, 0)] + [(2, 1, 0)] * 4 + [(2, 3, 0), (2, 2, 0), (4, 2, 0), (3, 0, 0), (3, 0, 0), (3, 0, 0), (7, 0, 0), (7, 1, 0), (2, 1, 0), (2, 1, 0), (2, 1, 0), (2, 3, 0), (7, 1, 0), (7, 0, 0), (7, 1, 0), (1, 500, 150), (1, 40, 100), (7, 1, 0), (3, 0, 0), (3, 0, 0)]   # 1-44: the quick-settings shade (3 tiles since 1-47: Dark, Text, Home -> the Tab counts changed by one)
EVENTS += [(7, 1, 0), (2, 1, 0), (2, 1, 0), (2, 3, 0), (2, 4, 0), (8, 3, 0), (8, 2, 0), (1, 180, 100), (8, 9, 0), (8, 1, 0), (3, 0, 0), (8, 0, 0)]   # 1-47 text size: open the shade, Tab to the Text tile, activate (130%), FONTSCALE next (200%), set 200% again (0), tap the Text tile (back to 100%), bad value, set 130%, BACK closes the shade, set 100%
EVENTS += [(9, 1, 0), (9, 1, 0), (7, 1, 0)] + [(2, 1, 0)] * 5 + [(2, 3, 0), (9, 1, 0), (4, 2, 0), (4, 2, 0), (1, 1000, 100), (9, 1, 0), (1, 500, 100), (9, 1, 0), (9, 5, 0), (9, 0, 0), (9, 0, 0), (7, 1, 0), (2, 1, 0), (9, 1, 0), (2, 1, 0), (9, 0, 0), (3, 0, 0)]
EVENTS += [(14, 0x0c00, 0), (14, 0x0c00, 0), (14, 0x1800, 0), (14, 0x173c, 0), (14, 0x10c00, 0), (14, 0x173b, 0), (14, 0x0000, 0)]   # 1-52 TIME: set, same, hour 24, minute 60, a bit above the 16-bit field, 23:59, 00:00   # 1-48 (the last six: open the shade, Tab to focus 0, POST clears the focus ring, Tab gives 0 again, clear, BACK closes the shade); 1-48 notification: post, post again, open the shade (card), Tab x5 to the clear button, activate it, post, THEME x2 (dark card), tap clear, post, tap the card body (HOME), post, bad value, clear, clear again
EXP = run_events(2, 1024, EVENTS)
assert EXP[-1][1] == hashes[(2, 0)] and EXP[65][1] == hashes[(2, 0)], 'after the whole interaction the band must be back to the plain expanded layout'
assert [c_ for c_, _h in EXP[20:28]] == [1, 257, 0, 1, 2, 0, 1, 0], EXP[20:28]
assert EXP[20][1] == hashes[(2, 0, 'd')] and EXP[21][1] == hashes[(2, 1, 'd')] and EXP[23][1] == hashes[(2, 1, 'd')] and EXP[24][1] == hashes[(2, 0, 'd')] and EXP[25][1] == EXP[24][1] and EXP[27][1] == EXP[26][1], 'dark states must equal the dark renders; failed BACK / repeated THEME must not change the screen'
assert EXP[22][1] not in (hashes[(2, 1, 'd')],), 'focus ring must change the dark screen'
assert EXP[0][1] == hashes[(2, 1)] and EXP[1][1] == EXP[0][1] and EXP[2][1] == EXP[0][1] and EXP[3][1] == EXP[0][1], 'misses must not change the screen'
assert [c_ for c_, _h in EXP[28:66]] == [1, 0, 0, 1, 2, 0, 1280, 2, 1, 1, 3, 0, 0, 1, 0, 1, 2, 1282, 1, 1, 1, 1281, 257, 1, 3, 2, 1, 0, 1, 1283, 0, 1, 0, 1283, -1, 1, 3, 1], EXP[28:66]   # open, open again, Tab x4 (THREE tiles: Dark, Text, Home; wraps), activate DARK, previous (wraps to Home), THEME toggle, BACK x3 (ring, shade, root), close-when-closed, open, Tab x3 + activate HOME (goes HOME), open/close/open, tap outside, FIND, open over FIND, BACK x2; 1-47: open, Tab x2 -> Text tile, activate (130%), FONTSCALE next (200%), set 200% again (0), tap the Text tile (100%), bad value (-1), set 130%, BACK closes the shade, set 100%
assert EXP[28][1] == hashes[(2, 2)] and EXP[29][1] == EXP[28][1], 'the shade screen equals the independent render; a second open changes nothing'
assert EXP[28 + 11][1] == hashes[(2, 0)] and EXP[28 + 12][1] == hashes[(2, 0)], 'after closing the shade the plain HOME is back (light) and a close-when-closed changes nothing'
assert EXP[28 + 17][1] == hashes[(2, 0)] and EXP[28 + 21][1] == hashes[(2, 0)], 'the HOME tile / a tap outside both end on the plain HOME'
assert EXP[28 + 23][1] == hashes[(2, 2)] and EXP[28 + 24][1] == hashes[(2, 1)], 'the shade opened over FIND, BACK returns to FIND, not HOME'
assert EXP[28 + 26][1] == hashes[(2, 2)] and EXP[28 + 30][1] == hashes[(2, 2, 's1')] and EXP[28 + 31][1] == hashes[(2, 2, 's2')] and EXP[28 + 32][1] == EXP[28 + 31][1], 'the Text tile / FONTSCALE re-lay the shade out at 130% then 200%; setting the same scale changes nothing'
assert EXP[28 + 33][1] == hashes[(2, 2)] and EXP[28 + 34][1] == EXP[28 + 33][1] and EXP[28 + 35][1] == hashes[(2, 2, 's1')] and EXP[28 + 36][1] == hashes[(2, 0, 's1')] and EXP[28 + 37][1] == hashes[(2, 0)], 'the tile wraps 200% -> 100%; a bad value changes nothing; BACK closes the shade onto the 130% HOME; 100% restores the plain HOME'
assert [c_ for c_, _h in EXP[66:]] == [1, 0, 1, 0, 1, 2, 3, 4, 1285, 1, 1, 1, 1285, 1, 1284, 1, -1, 1, 0, 1, 0, 1, 0, 1, 3, 1, 0, -1, -1, -1, 1, 1], EXP[66:]
NOTIF[0] = 1; _nh = {}
for _k in ((2, 0), (2, 2), (2, 2, 'd')):                       # the independent renders with a notification posted: HOME with the bell, the shade with the card, the dark shade with the card
    THEME[0] = 1 if 'd' in _k else 0; _nh[_k] = band_hash(render(2, 1024, _k[1])[0])
THEME[0] = 0; NOTIF[0] = 0
assert EXP[66][1] == _nh[(2, 0)] and EXP[67][1] == EXP[66][1] and EXP[68][1] == _nh[(2, 2)], 'POST shows the bell on HOME; posting again changes nothing; the shade shows the card'
assert EXP[74][1] == hashes[(2, 2)] and EXP[75][1] == _nh[(2, 2)] and EXP[76][1] == _nh[(2, 2, 'd')] and EXP[77][1] == _nh[(2, 2)], 'the clear button removes the card (the shade stays); the dark card equals the independent dark render'
assert EXP[78][1] == hashes[(2, 2)] and EXP[80][1] == hashes[(2, 0)] and EXP[81][1] == _nh[(2, 0)] and EXP[82][1] == EXP[81][1] and EXP[83][1] == hashes[(2, 0)] and EXP[84][1] == hashes[(2, 0)] and EXP[90][1] == hashes[(2, 0)], 'tap clear -> shade without the card; tap the card -> plain HOME; bell back; bad value changes nothing; clear -> plain HOME'
assert len({_nh[(2, 0)], _nh[(2, 2)], _nh[(2, 2, 'd')], hashes[(2, 0)], hashes[(2, 2)]}) == 5, 'the bell and the card each change the screen'
assert len({EXP[28 + k][1] for k in (26, 30, 31)}) == 3, 'three text sizes, three different screens'
assert len({h for _c, h in EXP}) >= 7, 'focus moves must change the screen'
print('events (code, hash): ' + ' '.join('(%d,0x%08x)' % (cd, h) for cd, h in EXP))
print('RUST codes: [' + ', '.join(str(cd) for cd, _h in EXP) + ']  hashes: [' + ', '.join('0x%08x' % h for _cd, h in EXP) + ']')
EV2 = [(1, 590, 120), (1, 400, 120), (1, 300, 120), (1, 40, 100)] + [(5, 0, 0)] * 17 + [(5, 255, 0), (5, 26, 0), (3, 0, 0)]   # medium window: FAB floating over card 2 (topmost wins), card 2 alone, card 1
EXP2 = run_events(1, 640, EV2)
assert [c_ for c_, _h in EXP2] == [0x400, 0x301, 0x300, 0x101] + list(range(1, 17)) + [-2, 15, 16, 2], EXP2
assert all(h == hashes[(1, 0)] for _c, h in EXP2[:3]) and EXP2[3][1] == hashes[(1, 1)] and EXP2[-1][1] == hashes[(1, 0)], 'medium: screen unchanged by clicks on cards, FIND shows the empty search bar, BACK returns to the plain HOME'
assert EXP2[20][1] == EXP2[19][1], 'a full field must not change the screen'
print('RUST medium codes: [' + ', '.join(str(cd) for cd, _h in EXP2) + ']  hashes: [' + ', '.join('0x%08x' % h for _cd, h in EXP2) + ']')

EV3 = [(1, 40, 100)] + [(5, 22, 0)] * 16 + [(3, 0, 0)]                    # 1-46, COMPACT window (360 dp: the field is 212 dp wide): sixteen capital W's -- the 16th no longer fits the 212-dp field by WIDTH (15 x 14 = 210 fit, 16 x 14 = 224 do not) although the character cap (16) is not yet reached
EXP3 = run_events(0, 360, EV3)
fit = max(n for n in range(1, 17) if tw_(2, 'W' * n) <= 360 - 148)
assert fit < 16 and [c_ for c_, _h in EXP3] == [0x101] + list(range(1, fit + 1)) + [-2] * (16 - fit) + [2], (fit, EXP3)
assert fit < 16 and EXP3[fit + 1][1] == EXP3[fit][1], 'a refused character must not change the screen'
assert EXP3[-1][1] == hashes[(0, 0)] and EXP3[0][1] == hashes[(0, 1)], 'compact: FIND shows the empty search bar, BACK returns to the plain HOME'
print('RUST compact codes: [' + ', '.join(str(cd) for cd, _h in EXP3) + ']  hashes: [' + ', '.join('0x%08x' % h for _cd, h in EXP3) + ']')

# ---- 1-38: keyboard path. A scripted PS/2 (scancode set 1) byte stream is decoded and translated by an INDEPENDENT model of the router (UI_DESIGN_LANGUAGE.md section 5), then run through the shell model. ----
KBD = [0x0f, 0x8f, 0x1c, 0x9c, 0x0f, 0x8f, 0x1c, 0x9c, 0x2a, 0x0f, 0x8f, 0xaa, 0x3c, 0xbc, 0x2a, 0x23, 0xa3, 0xaa, 0x12, 0x92, 0x26, 0xa6, 0x26, 0xa6, 0x18, 0x98, 0x39, 0xb9, 0x11, 0x91, 0x0e, 0x8e, 0x0e, 0x8e, 0x01, 0x81, 0x01, 0x81, 0x01, 0x81, 0x3c, 0xbc, 0x10, 0x90, 0xe0, 0x4d, 0xe0, 0xcd, 0xe0, 0x4b, 0xe0, 0xcb, 0x01, 0x81] + [0x3d, 0xbd] * 3 + [0x3e, 0xbe] * 2   # 1-48: F4 twice (post, clear: the bell appears and goes);  # 1-47: F3 x3 at the end of the keyboard part (the band is the plain HOME then): next text size 130%, 200%, back to 100%
# PS/2 set-1 make codes of the letter keys, written out from the standard layout (QWERTY rows), NOT read from keymap in font5x7.inc
SC_LETTERS = {c: ord(ch) - 65 for row, base in (('QWERTYUIOP', 0x10), ('ASDFGHJKL', 0x1e), ('ZXCVBNM', 0x2c)) for c, ch in zip(range(base, base + len(row)), row)}
def decode_set1(bs):
    ev, ext = [], False
    for b in bs:
        if b == 0xe0: ext = True; continue
        ev.append((((0xe000 if ext else 0) | (b & 0x7f)), not (b & 0x80))); ext = False
    return ev
def route(evs):
    shift = dark = 0; out = []; ignored = 0
    for code, pressed in evs:
        if code in (0x2a, 0x36): shift = int(pressed); continue
        if not pressed: ignored += 1; continue
        if code == 0x0f: out.append((2, 2 if shift else 1, 0))
        elif code == 0xe04d: out.append((2, 1, 0))
        elif code == 0xe04b: out.append((2, 2, 0))
        elif code == 0x1c: out.append((2, 3, 0))
        elif code == 0x01: out.append((3, 0, 0))
        elif code == 0x3c: dark ^= 1; out.append((4, 2, 0))          # F2 = toggle (a = 2)
        elif code == 0x3e: out.append((9, 2, 0))                       # F4 = post / clear the notification (POST a = 2: the shell owns the state, 1-48)
        elif code == 0x3d: out.append((8, 3, 0))                       # F3 = next text size (FONTSCALE a = 3)
        elif code == 0x0e: out.append((5, 255, 0))
        elif code == 0x39: out.append((5, 26, 0))
        elif code in SC_LETTERS: out.append((5, SC_LETTERS[code] + (0 if shift else 32), 0))     # lowercase unless Shift is held
        else: ignored += 1
    return out, ignored, shift, dark
KEV = decode_set1(KBD); KOUT, KIGN, KSH, KDK = route(KEV)
# ---- 1-41: PS/2 mouse. Packets are built here, decoded by an independent decoder, and integrated by an independent router model ----
def pk(dx, dy, btn): return [0x08 | btn | (0x10 if dx < 0 else 0) | (0x20 if dy < 0 else 0), dx & 0xff, dy & 0xff]
MOUSE = [0x00] + sum([pk(-236, 0, 0), [0x48, 0x7f, 0x00], pk(-236, -4, 0), pk(0, 0, 1), pk(0, 0, 0), pk(0, 60, 0), pk(0, 0, 1), pk(0, 0, 0)]      # a stray byte first (the decoder must resynchronise on bit 3), then: left to the FIND rail item, click, up to HOME, click
    + [pk(120, 0, 0)] * 10 + [pk(-30, 0, 0), pk(0, 0, 1), pk(0, 0, 1), pk(0, 0, 0)]
    + [pk(30, 0, 0), pk(0, 0, 1)] + [pk(-31, 0, 1), pk(-32, 0, 1), pk(-1, 0, 1), pk(-32, 0, 1)] + [pk(0, 0, 0)]                                                      # 1-42: press in the RIGHT edge zone at x=1023, drag left 31,32,1,32: 63 px does not fire, 64 does (once), no click; release
    + [pk(-120, 0, 0)] * 9 + [pk(0, 0, 1), pk(32, 0, 1), pk(32, 0, 1), pk(30, 0, 1), pk(0, 0, 0)]                           # LEFT edge: press at x=0, drag 32+32 = EXACTLY 64 px: BACK fires (boundary), the 3rd drag does not repeat it
    + [pk(-127, 0, 0), pk(0, 0, 1), pk(31, 0, 1), pk(32, 0, 1), pk(0, 0, 0)]                                                 # press at 0 again, drag 31+32 = 63 px: one pixel short, cancelled: no BACK, no click
    + [pk(27, 0, 0), pk(0, 0, 1), pk(100, 0, 1), pk(0, 0, 0)]
    + [pk(-127, 0, 0), pk(-47, 0, 0), pk(0, 0, 1), pk(80, 0, 1), pk(0, 0, 0)]                                              # 1-43 zone edges: press at x=16 (JUST outside the left zone): a click, the 80-px drag does nothing
    + [pk(-81, 0, 0), pk(0, 0, 1), pk(64, 0, 1), pk(0, 0, 0)]                                                              # press at x=15 (the last pixel inside): a gesture, 64 px of drag fires BACK
    + [pk(127, 0, 0)] * 8 + [pk(-15, 0, 0), pk(0, 0, 1), pk(-64, 0, 1), pk(0, 0, 0)]                                       # right: to 1023 (clamp), back to 1008 (the first pixel inside the right zone): gesture, BACK at 64 px
    + [pk(127, 0, 0), pk(-16, 0, 0), pk(0, 0, 1), pk(-80, 0, 1), pk(0, 0, 0)]
    # 1-44 TOP edge (y <= 15): press at (927,15) (the pointer is at y=40: 25 up), drag DOWN 32+32 = 64 px: the quick-settings shade opens (once; the 3rd drag does not repeat it); then a tap outside the tiles closes it
    + [pk(0, 25, 0), pk(0, 0, 1), pk(0, -32, 1), pk(0, -32, 1), pk(0, -30, 1), pk(0, 0, 0), pk(0, 0, 1), pk(0, 0, 0)]
    # press at y=15 again, drag 31+32 = 63 px: one short, cancelled (no shade, no click); then press at y=16 (just OUTSIDE the zone): an ordinary click (a miss), the 80-px drag does nothing
    + [pk(0, 94, 0), pk(0, 0, 1), pk(0, -31, 1), pk(0, -32, 1), pk(0, 0, 0), pk(0, 62, 0), pk(0, 0, 1), pk(0, -80, 1), pk(0, 0, 0)]
    # open the shade again, walk to the DARK tile (100,100), click (dark on), click (dark off); to the HOME tile (250,100), click (-> HOME); open again, walk to the left edge, swipe right 64 px = BACK closes the shade
    + [pk(0, 81, 0), pk(0, 0, 1), pk(0, -64, 1), pk(0, 0, 0)] + [pk(-127, 0, 0)] * 6 + [pk(-65, -21, 0), pk(0, 0, 1), pk(0, 0, 0), pk(0, 0, 1), pk(0, 0, 0), pk(150, 0, 0), pk(0, 0, 1), pk(0, 0, 0)]
    + [pk(0, 85, 0), pk(0, 0, 1), pk(0, -64, 1), pk(0, 0, 0), pk(-127, 0, 0), pk(-123, 0, 0), pk(0, 0, 1), pk(32, 0, 1), pk(32, 0, 1), pk(0, 0, 0)], [])                                         # press at x=1007 (just outside): a click, the drag does nothing                                                           # press at x=90 (outside the zone): an ordinary click, the drag does nothing                                       # then right past the window edge (clamps at 1023), back 30, a click HELD over two packets (one click only), release
def decode_mouse(bs):
    out, buf = [], []
    for b in bs:                                   # independent packet assembler: while aligned, a byte without bit 3 is skipped
        if not buf and not b & 8: continue
        buf.append(b)
        if len(buf) == 3:
            st, bx, by = buf; buf = []
            if st & 0xc0: continue                    # overflow packets are dropped, never turned into a movement
            out.append((bx - (256 if st & 0x10 else 0), by - (256 if st & 0x20 else 0), st & 7))
    return out
MEV = decode_mouse(MOUSE)
def route_mouse(evs):
    x, y, last, out, g, sx, sy = 512, 96, 0, [], 0, 0, 0
    for dx, dy, b in evs:
        nx, ny = min(max(x + dx, 0), 1023), min(max(y - dy, 0), 191)
        if (nx, ny) != (x, y): x, y = nx, ny; out.append((6, x, y))
        if not (b & 1): g = 0
        elif not last:
            if x <= 15: g, sx = 1, x
            elif x >= 1008: g, sx = 2, x
            elif y <= 15: g, sy = 4, y                       # 1-44: top edge zone
            else: g = 0; out.append((1, x, y))
        elif (g == 1 and x - sx >= 64) or (g == 2 and sx - x >= 64): g = 3; out.append((3, 0, 0))
        elif g == 4 and y - sy >= 64: g = 3; out.append((7, 1, 0))
        last = b & 1
    return out
MOUT = route_mouse(MEV)
KOUT_ALL = KOUT + MOUT
EXPK = run_events(2, 1024, KOUT_ALL)
NK = len(KOUT)
assert [c_ for c_, _h in EXPK[:NK]] == [0, 256, 1, 257, 0, 1] + [1, 2, 3, 4, 5, 6, 7, 6, 5] + [1, 2, 0, 1, -1, 0, 4, 1] + [1, 1, 1] + [1, 1], EXPK   # F3 x3 (1-47); focus + navigation, then typing, then Esc x3, F2 light, a rejected key in HOME, Right, Left (wraps to 4), Esc
assert EXPK[NK - 1][1] == hashes[(2, 0)], 'the keyboard part of the script must end on the plain light HOME screen'
assert EXPK[NK - 5][1] == hashes[(2, 0, 's1')] and EXPK[NK - 4][1] == hashes[(2, 0, 's2')] and EXPK[NK - 3][1] == hashes[(2, 0)] and EXPK[NK - 2][1] == _nh[(2, 0)], 'F3 steps the whole UI through 130% and 200% text and back to 100%'
# the mouse part (1-41), after the keyboard part in the same queue: MOVE (276,96), MOVE (40,100), click -> FIND (0x101), MOVE (40,40), click -> HOME (0x100); the cursor stays drawn
TAIL = [c_ for c_, _h in EXPK[NK:]]
assert TAIL[:5] == [96 << 16 | 276, 100 << 16 | 40, 0x101, 40 << 16 | 40, 0x100], TAIL
GEST = [40 << 16 | x for x in (1023, 992, 960, 959)] + [0] + [40 << 16 | 927]           # 1-42 right-edge swipe: press at 1023 is not a click; BACK (reply 0 = HOME already) fires once at 959 (64 px), NOT at 960 (63 px); the 4th drag does not repeat it
GEST += [40 << 16 | x for x in (807, 687, 567, 447, 327, 207, 87, 0, 32, 64)] + [0] + [40 << 16 | x for x in (94, 0, 31, 63, 90)]   # left edge: 8 moves left (clamp at 0); 64 px of drag fires BACK exactly once (then 94 does not repeat it); 63 px does not fire; no click either time
GEST += [-1, 40 << 16 | 190]                                                              # press at x=90 is outside the zone: an ordinary POINT (a miss, -1); the 100-px drag afterwards does nothing
mv = lambda *xs: [40 << 16 | x for x in xs]
GEST += mv(63, 16) + [0x100] + mv(96, 15, 79) + [0] + mv(206, 333, 460, 587, 714, 841, 968, 1023, 1008, 944) + [0] + mv(1023, 1007) + [0x200] + mv(927)   # 1-43: x=16 / 1007 are one pixel OUTSIDE the zones (an ordinary click: 0x100 / 0x200, the drag after it does nothing); x=15 / 1008 are the first pixels INSIDE (a gesture, BACK at 64 px)
my = lambda *pts: [y << 16 | x for (x, y) in pts]
GEST += my((927, 15), (927, 47), (927, 79)) + [1] + my((927, 109)) + [0x501]                                   # 1-44 top edge: press at y=15 (the pointer was at y=40), drag 32+32 = 64 px: SHADE opens (reply 1) once; the 3rd drag does not repeat it; a tap outside the tiles closes it (0x501)
GEST += my((927, 15), (927, 46), (927, 78), (927, 16)) + [-1] + my((927, 96))                                    # 63 px of drag: no shade; a press at y=16 (outside the zone) is an ordinary click: a miss (-1), the 80-px drag does nothing
GEST += my((927, 15), (927, 79)) + [1] + my((800, 79), (673, 79), (546, 79), (419, 79), (292, 79), (165, 79), (100, 100)) + [0x500, 0x500] + my((250, 100)) + [0x502]   # open; walk to the DARK tile and click twice (dark on, off); the HOME tile closes the shade
GEST += my((250, 15), (250, 79)) + [1] + my((123, 79), (0, 79), (32, 79), (64, 79)) + [3]                      # open again; left-edge swipe of 64 px = BACK, which closes the shade (reply 3)
assert TAIL[5:] == [40 << 16 | x for x in (160, 280, 400, 520, 640, 760, 880, 1000, 1023, 993)] + [0x200] + GEST, TAIL   # 9 moves, the clamp at 1023, back to 993, ONE click (0x200: the trailing action) although the button stays down for two packets
fb_, _t, _h = render(2, 1024, 0, -1, '', (40, 40)); assert EXPK[NK + 4][1] == band_hash(fb_), 'after the HOME click the band is HOME with the cursor at (40,40)'
fb_, _t, _h = render(2, 1024, 1, -1, 'Hello', (40, 100)); assert EXPK[NK + 2][1] == band_hash(fb_), 'after the first click the band is FIND with the cursor at (40,100)'
fb_, _t, _h = render(2, 1024, 0, -1, '', (1023, 40)); assert EXPK[NK + 5 + 8][1] == band_hash(fb_) and (1023, 40) == (min(1023, 1024 - 1), 40), 'at the clamp the cursor is kept inside the window (drawn at W-6)'
print('KBD bytes (%d): [%s]' % (len(KBD), ', '.join('0x%02x' % b for b in KBD)))
print('RUST kbd script (%d): [%s]' % (len(KBD), ', '.join('0x%02x' % b for b in KBD)))
print('KBD decoded events %d, forwarded %d, ignored %d, final shift/dark %d/%d' % (len(KEV), len(KOUT), KIGN, KSH, KDK))
print('MOUSE bytes (%d): [%s]  events %d, forwarded %d' % (len(MOUSE), ', '.join('0x%02x' % b for b in MOUSE), len(MEV), len(MOUT)))
print('RUST kbd codes: [' + ', '.join(str(c_) for c_, _h in EXPK) + ']  hashes: [' + ', '.join('0x%08x' % h for _c, h in EXPK) + ']')
fbs = {c: fbs[(c, 0)] for c in range(3)}
if '--png' in sys.argv:
    out = sys.argv[sys.argv.index('--png') + 1]
    from PIL import Image
    im = Image.new('RGB', (FBW, ROWS * 3 + 8 * 2), (24, 24, 24))
    for c in range(3):
        px = Image.new('RGB', (FBW, ROWS)); px.putdata([((p >> 16) & 255, (p >> 8) & 255, p & 255) for row in fbs[c] for p in row]); im.paste(px, (0, c * (ROWS + 8)))
    im.save(out); print('wrote', out)
if '--png-states' in sys.argv:
    from PIL import Image
    out = sys.argv[sys.argv.index('--png-states') + 1]
    states = [(2, 1024, 0, -1, 0), (2, 1024, 1, -1, 0), (2, 1024, 1, 3, 0), (1, 640, 0, 4, 0), (2, 1024, 0, -1, 1), (2, 1024, 1, 3, 1), (2, 1024, 1, -1, 0, 'Hello'), (0, 360, 1, -1, 1, 'Hello world')]
    im = Image.new('RGB', (FBW, (ROWS + 8) * len(states) - 8), (24, 24, 24))
    for k, st in enumerate(states):
        c, W, act, foc, dk = st[:5]; txt = st[5] if len(st) > 5 else ''
        THEME[0] = dk; fb, _t, _h = render(c, W, act, foc, txt); THEME[0] = 0; px = Image.new('RGB', (FBW, ROWS)); px.putdata([((p >> 16) & 255, (p >> 8) & 255, p & 255) for row in fb for p in row]); im.paste(px, (0, k * (ROWS + 8)))
    im.save(out); print('wrote', out)

if '--dump-ppm' in sys.argv:        # --dump-ppm ACTIVE FOCUS DARK TEXT OUT : the expected 1024x192 expanded-window band as a binary PPM (TEXT '-' = empty); used by the live keyboard test
    i = sys.argv.index('--dump-ppm'); act, foc, dk, txt, out = int(sys.argv[i + 1]), int(sys.argv[i + 2]), int(sys.argv[i + 3]), sys.argv[i + 4], sys.argv[i + 5]
    cur = tuple(int(v) for v in sys.argv[i + 6].split(',')) if len(sys.argv) > i + 6 else None
    SCALE[0] = int(os.environ.get('NY_SCALE', '0')); NOTIF[0] = int(os.environ.get('NY_NOTIF', '0')); THEME[0] = dk; fb, _t, _h = render(2, 1024, act, foc, '' if txt == '-' else txt, cur); THEME[0] = 0; SCALE[0] = 0; NOTIF[0] = 0
    with open(out, 'wb') as f:
        f.write(b'P6\n1024 192\n255\n'); f.write(bytes(v for row in fb for p in row for v in ((p >> 16) & 255, (p >> 8) & 255, p & 255)))
