#!/usr/bin/env python3
"""NDL-1 layout engine (build time): turns the Nyrqis design rules (UI_DESIGN_LANGUAGE.md) into the display lists the user-space `shell` renders.
Output: userprog/shell_layout.inc. One list per WINDOW WIDTH CLASS (Android/Material: compact < 600 dp, medium 600..839, expanded >= 840), all for a
COMPACT-HEIGHT window (192 dp: the band the demo owns), so every class uses a navigation RAIL (Material: compact height => rail, not a bottom bar).
Record = 8 little-endian dwords: type | cond<<8 (type: 0 end,1 rect,2 rounded rect,3 text,4 hit box = not drawn, aux = action id; cond: 0 always, n = only while destination n-1 is active, n|0x80 = only while it is not), x, y, w, h, radius, colour, aux (text: string offset from shell_base).
The independent lint is tools/check_shell.py; it shares NO code with this file."""
import math, sys, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import svgraster, fontgen
GLYPH_MASKS, GLYPH_TABLE, GLYPH_HEADS = fontgen.build()
def fw(face, t): return fontgen.width(GLYPH_TABLE, face, t)          # pen advance of a string in a face (the shell sums the same advances)
LABEL, SMALL, BODY, TITLE = 0, 1, 2, 3                                      # Material type roles: label-medium 12/500, body-medium 14, body-large 16, title-large 22
ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'third_party', 'material-icons')   # the official Google Material icons (Apache-2.0), vendored unmodified
ICONS, ICON_TAB = {}, bytearray()
def ic(name, variant, size, rot=0):
    """-> byte offset of the size x size alpha mask of that Material icon in the `icons` table (rasterized once, build time)."""
    k = (name, variant, size, rot)
    if k not in ICONS:
        ICONS[k] = len(ICON_TAB); ICON_TAB.extend(svgraster.raster(svgraster.load(os.path.join(ICON_DIR, variant, name + '.svg')), size, rot))
    return ICONS[k]
SURFACE, CONT, PRIMARY_CONT, SEC_CONT = 0xFEF7FF, 0xF3EDF7, 0xEADDFF, 0xE8DEF8
ON_SURF, ON_VAR, ON_PC = 0x1D1B20, 0x49454F, 0x4F378B
H = 192
RADII = [2, 4, 8, 12, 16, 28, 20, 24]            # 1-51: 20 (avatars) and 24 (pill buttons) appended, so the offsets of the older radii do not move
HOME_, FIND_ = 1, 2                                   # cond: draw only while that destination is active (1 = HOME, 2 = FIND)
NOT = 0x80                                            # cond | NOT: draw only while it is NOT active
HIDE = 0x40                                           # cond | HIDE: not drawn / not a target while the QUICK-SETTINGS SHADE (destination 2, cond 3) is open
SHADE_ = 3
NOTIF = 0x20                                          # cond | NOTIF: drawn / a target only while a notification is posted (1-48)
LAUNCH_, RECENTS_ = 4, 5                              # 1-51 (tall windows only): the launcher (home screen) and the recents screen are destinations 3 and 4
TASK, NOTASK, EMPTY = 0x100, 0x200, 0x400
BTN, GST = 0x800, 0x1000                              # 1-51: cond | BTN: only in 3-BUTTON navigation mode; cond | GST: only in GESTURE navigation mode (the default, as on Android 17)             # 1-51: cond | TASK: only while the Messages task exists; NOTASK: only while it does not; EMPTY: only while the search field is empty
WALL, WHITE, DIM = 0x31264F, 0xFFFFFF, 0xE6E0E9       # launcher wallpaper (one flat colour) and the colours drawn over it
MSGS = (("Weekly plan", "No pending items"), ("Team notes", "Updated yesterday"), ("Travel", "Passes ready"), ("Receipts", "2 new files"), ("Reading list", "12 saved"))
def layout(W, sc):
    L = []
    def face(role): return 4 * sc + role                                    # absolute face id: 4 roles x 3 font scales (fontgen.FACES)
    def lh(role): return sum(GLYPH_HEADS[face(role)])
    def base(top, box, role): return top + (box - lh(role)) // 2 + GLYPH_HEADS[face(role)][0]      # BASELINE of a single line centred vertically in [top, top + box)
    def T(x, baseline, role, col, txt, cond=0):                              # a text record: y = baseline, r = absolute face id
        L_add(3, x, baseline, 0, 0, face(role), col, txt, cond, 0)
    def L_add(t, x, y, w, h, r, col, txt=None, cond=0, aux=0):
        if (cond == 0 or cond & NOT) and L: cond |= HIDE                    # everything except the window rect yields to the shade
        L.append((t | (cond << 8), x, y, w, h, r, col, txt, aux))
    def R(t, x, y, w, h, r, col, txt=None, cond=0, aux=0): L_add(t, x, y, w, h, r, col, txt, cond, aux)
    def status_icons(cond=0):                                              # Material status-bar glyphs, 16 dp: cellular, Wi-Fi, battery (turned to lie flat)
        R(5, W - 80, 4, 16, 16, 0, ON_VAR, cond=cond, aux=ic('signal_cellular_4_bar', 'filled', 16)); R(5, W - 60, 4, 16, 16, 0, ON_VAR, cond=cond, aux=ic('wifi', 'filled', 16)); R(5, W - 40, 4, 16, 16, 0, ON_VAR, cond=cond, aux=ic('battery_full', 'filled', 16, 90))
        R(5, W - 100, 4, 16, 16, 0, ON_VAR, cond=(cond or HIDE) | NOTIF, aux=ic('notifications', 'filled', 16))      # 1-48: the status-bar bell, only while a notification is posted
    R(1, 0, 0, W, H, 0, SURFACE)                                         # window (edge to edge: the status bar is drawn on the same surface)
    status_icons()
    for i, (lab, gl) in enumerate((("Home", 'home'), ("Find", 'search'))):                             # navigation rail, 80 dp wide, 56 dp items, above the 24 dp gesture inset
        y = 24 + 56 * i; tw = fw(face(LABEL), lab); assert tw <= 72 and lh(LABEL) <= 28, ('rail label does not fit', sc, lab)
        R(4, 0, y, 80, 56, 0, 0, aux=0x100 + i)                            # hit box: activates destination i
        R(2, 12, y, 56, 32, 16, SEC_CONT, cond=i + 1)                      # active indicator (pill)
        R(5, 28, y + 4, 24, 24, 0, ON_SURF, cond=i + 1, aux=ic(gl, 'filled', 24)); T(40 - tw // 2, base(y + 28, 28, LABEL), LABEL, ON_SURF, lab, i + 1)       # Material 3: the active destination is the FILLED glyph, inactive the OUTLINED one
        R(5, 28, y + 4, 24, 24, 0, ON_VAR, cond=NOT | (i + 1), aux=ic(gl, 'outlined', 24)); T(40 - tw // 2, base(y + 28, 28, LABEL), LABEL, ON_VAR, lab, NOT | (i + 1))
    T(96, base(24, 64, TITLE), TITLE, ON_SURF, "Inbox", HOME_); T(96, base(24, 64, TITLE), TITLE, ON_SURF, "Search", FIND_)   # top app bar (64 dp) title
    for k, dx in enumerate((40, 88) if W >= 840 else (40,)):              # trailing actions: 48 dp targets with 24 dp icons
        R(4, W - dx - 12, 32, 48, 48, 0, 0, aux=0x200 + k); R(5, W - dx, 44, 24, 24, 0, ON_VAR, aux=ic(('more_vert', 'search')[k], 'filled', 24))
    if W < 600: cards = [(96, 96, W - 96 - 16, 48, "Weekly plan", CONT)]
    elif W < 840:
        cw = (W - 96 - 16 - 16) // 2
        cards = [(96, 96, cw, 48, "Weekly plan", CONT), (96 + cw + 16, 96, cw, 48, "Team notes", CONT)]
    else: cards = [(96, 96, 288, 48, "Weekly plan", SEC_CONT)]             # list pane (selected item) + detail pane
    for n, (x, y, w, h, t, c) in enumerate(cards):
        assert lh(BODY) <= h and 16 + fw(face(BODY), t) <= w - 16, ('card text does not fit', sc, t)
        R(2, x, y, w, h, 12, c, cond=HOME_); R(4, x, y, w, h, 0, 0, cond=HOME_, aux=0x300 + n); T(x + 16, base(y, h, BODY), BODY, ON_SURF, t, HOME_)
    R(2, 96, 96, W - 96 - 16, 56, 28, CONT, cond=FIND_); R(4, 96, 96, W - 96 - 16, 56, 0, 0, cond=FIND_, aux=0x310)   # FIND: a search bar (extra-large shape)
    T(120, base(96, 56, BODY), BODY, ON_VAR, "Search messages", FIND_)
    if W >= 840:
        R(2, 400, 96, W - 400 - 16, 80, 28, CONT, cond=HOME_)              # detail pane (extra-large shape): two lines, centred as a block
        blk = lh(BODY) + 4 + lh(SMALL); top = 96 + (80 - blk) // 2; assert blk <= 72, ('detail pane text does not fit', sc)
        T(424, top + GLYPH_HEADS[face(BODY)][0], BODY, ON_SURF, "Weekly plan", HOME_); T(424, top + lh(BODY) + 4 + GLYPH_HEADS[face(SMALL)][0], SMALL, ON_VAR, "No pending items", HOME_)
    else:
        fx, fy = W - 16 - 56, 104                                           # FAB: 56 dp, large shape, above the gesture inset
        R(2, fx, fy, 56, 56, 16, PRIMARY_CONT, cond=HOME_); R(4, fx, fy, 56, 56, 0, 0, cond=HOME_, aux=0x400)
        R(5, fx + 16, fy + 16, 24, 24, 0, ON_PC, cond=HOME_, aux=ic('add', 'filled', 24))
    R(2, (W - 108) // 2, 180, 108, 4, 2, ON_VAR)                            # gesture pill (system navigation, gesture mode)
    # QUICK-SETTINGS SHADE (1-44, three tiles since 1-47): opened by a swipe down from the top edge; covers the whole window; three 56-dp pill tiles (dark theme, text size, home); a tap outside the tiles closes it
    R(1, 0, 0, W, H, 0, CONT, cond=SHADE_)
    status_icons(SHADE_)
    T(24, base(24, 44, TITLE), TITLE, ON_SURF, "Quick settings", SHADE_); assert 24 + lh(TITLE) <= 72
    for k, (lab, act, gl) in enumerate((("Dark", 0x500, 'dark_mode'), ("Text", 0x503, 'format_size'), ("Home", 0x502, 'home'))):
        tx = 16 + 112 * k; assert 40 + fw(face(LABEL), lab) <= 104 - 8 and lh(LABEL) <= 48, ('tile label does not fit', sc, lab)
        R(2, tx, 72, 104, 56, 28, SEC_CONT, cond=SHADE_); R(4, tx, 72, 104, 56, 0, 0, cond=SHADE_, aux=act)
        R(5, tx + 10, 88, 24, 24, 0, ON_SURF, cond=SHADE_, aux=ic(gl, 'outlined', 24)); T(tx + 40, base(72, 56, LABEL), LABEL, ON_SURF, lab, SHADE_)      # Android QS tile: glyph + label
    if W >= 600:                                                           # 1-48 the NOTIFICATION CARD (split shade, as on Android large screens: tiles left, notifications right). Compact windows have no room in the 192-dp band: only the bell shows there
        cx, cw = 360, W - 360 - 16; C = SHADE_ | NOTIF; assert 48 + fw(face(BODY), "Welcome") <= cw - 48 and lh(BODY) <= 56, ('notification text does not fit', sc)
        R(2, cx, 72, cw, 56, 12, SURFACE, cond=C); R(4, cx, 72, cw - 48, 56, 0, 0, cond=C, aux=0x504)     # tap the card: open it (dismisses it, closes the shade, goes HOME)
        R(4, W - 16 - 48, 76, 48, 48, 0, 0, cond=C, aux=0x505)                                             # the clear button: dismiss (the shade stays)
        R(5, cx + 12, 88, 24, 24, 0, ON_SURF, cond=C, aux=ic('notifications', 'outlined', 24)); T(cx + 48, base(72, 56, BODY), BODY, ON_SURF, "Welcome", C)
        R(5, W - 16 - 48 + 12, 88, 24, 24, 0, ON_VAR, cond=C, aux=ic('close', 'outlined', 24))
    R(2, (W - 108) // 2, 180, 108, 4, 2, ON_VAR, cond=SHADE_)
    return L
HT, NAVY, NAVH, SBH = 768, 720, 48, 24                # 1-51 tall windows: the whole 1024x768 screen; status bar 24 dp on top, the 48-dp system navigation bar at the bottom
def layout_tall(W, sc):
    """A FULL-SCREEN window (1024x768 framebuffer, W dp wide; the rest of the screen is black): Android system chrome + the Messages app + launcher + recents + quick settings."""
    L = []
    cls = 0 if W < 600 else (1 if W < 840 else 2)
    def face(role, s=None): return 4 * (sc if s is None else s) + role
    def lh(role, s=None): return sum(GLYPH_HEADS[face(role, s)])
    def asc(role, s=None): return GLYPH_HEADS[face(role, s)][0]
    def base(top, box, role, s=None): return top + (box - lh(role, s)) // 2 + asc(role, s)
    def A(t, x, y, w, h, r, col, txt=None, cond=0, aux=0): L.append((t | (cond << 8), x, y, w, h, r, col, txt, aux))
    def rect(x, y, w, h, col, cond): A(1, x, y, w, h, 0, col, cond=cond)
    def rr(x, y, w, h, r, col, cond): assert 2 * r <= min(w, h), (x, y, w, h, r); A(2, x, y, w, h, r, col, cond=cond)
    def T(x, baseline, role, col, txt, cond, s=None): A(3, x, baseline, 0, 0, face(role, s), col, txt, cond)
    def icon(x, y, size, col, name, variant, cond): A(5, x, y, size, size, 0, col, cond=cond, aux=ic(name, variant, size))
    def hit(x, y, w, h, act, cond): A(4, x, y, w, h, 0, 0, cond=cond, aux=act)
    def tw(role, txt, s=None): return fw(face(role, s), txt)
    A(1, 0, 0, 1024, HT, 0, 0x000000, cond=0)                            # the screen outside the window is black (also after the window got narrower)
    BG = {HOME_: SURFACE, FIND_: SURFACE, SHADE_: CONT, LAUNCH_: WALL, RECENTS_: CONT}
    FG = {HOME_: ON_SURF, FIND_: ON_SURF, SHADE_: ON_SURF, LAUNCH_: WHITE, RECENTS_: ON_SURF}
    FGV = {HOME_: ON_VAR, FIND_: ON_VAR, SHADE_: ON_VAR, LAUNCH_: WHITE, RECENTS_: ON_VAR}
    def status(d):                                                       # Material/Android status bar: the clock on the left (system text is NOT scaled with the text size, as on Android), the icons on the right
        A(3, 16, base(0, SBH, LABEL, 0), 0, 0, face(LABEL, 0), FG[d], None, d, -1)      # aux = -1: the CLOCK string, drawn by the shell from the time `rtcd` reports (1-52)
        icon(W - 80, 4, 16, FGV[d], 'signal_cellular_4_bar', 'filled', d); icon(W - 60, 4, 16, FGV[d], 'wifi', 'filled', d); icon(W - 40, 4, 16, FGV[d], 'battery_full', 'filled', d)
        icon(W - 100, 4, 16, FGV[d], 'notifications', 'filled', d | NOTIF)
    fx = 16 if cls == 0 else 96
    def app(d):
        rect(0, 0, W, NAVY, SURFACE, d); status(d)
        if cls == 0:                                                     # compact: Material 3 NAVIGATION BAR (80 dp) with two destinations
            rect(0, 640, W, 80, CONT, d)
            for i, (lab, gl) in enumerate((("Home", 'home'), ("Find", 'search'))):
                x0 = i * (W // 2); cx = x0 + W // 4; tw_ = tw(LABEL, lab); assert tw_ <= W // 2 - 8 and 8 + 32 + 4 + lh(LABEL) <= 80, ('bottom bar label', sc)
                hit(x0, 640, W // 2, 80, 0x100 + i, d)
                if d == i + 1: rr(cx - 32, 648, 64, 32, 16, SEC_CONT, d)
                icon(cx - 12, 652, 24, ON_SURF if d == i + 1 else ON_VAR, gl, 'filled' if d == i + 1 else 'outlined', d)
                T(cx - tw_ // 2, base(680, 40, LABEL), LABEL, ON_SURF if d == i + 1 else ON_VAR, lab, d)
        else:                                                            # medium / expanded: NAVIGATION RAIL (80 dp)
            for i, (lab, gl) in enumerate((("Home", 'home'), ("Find", 'search'))):
                y = 48 + 64 * i; tw_ = tw(LABEL, lab); assert tw_ <= 72 and lh(LABEL) <= 28, ('rail label', sc)
                hit(0, y, 80, 56, 0x100 + i, d)
                if d == i + 1: rr(12, y, 56, 32, 16, SEC_CONT, d)
                icon(28, y + 4, 24, ON_SURF if d == i + 1 else ON_VAR, gl, 'filled' if d == i + 1 else 'outlined', d)
                T(40 - tw_ // 2, base(y + 28, 28, LABEL), LABEL, ON_SURF if d == i + 1 else ON_VAR, lab, d)
        if d == HOME_:
            T(fx, base(SBH, 64, TITLE), TITLE, ON_SURF, "Inbox", d)
            for k, dx in enumerate((64, 112) if cls == 2 else (64,)):
                hit(W - dx, 32, 48, 48, 0x200 + k, d); icon(W - dx + 12, 44, 24, ON_VAR, ('more_vert', 'search')[k], 'filled', d)
            rh = -(-(lh(BODY) + 4 + lh(SMALL) + 24) // 8) * 8; rh = max(rh, 72)
            lx0 = 0 if cls == 0 else (96 if cls == 1 else 104); lw = W if cls == 0 else (W - 96 - 16 if cls == 1 else 344)
            n = min(len(MSGS), (640 - 96) // rh if cls == 0 else (712 - 96) // rh)
            for k in range(n):
                y = 96 + k * rh; (hl, sub) = MSGS[k]
                if cls == 2 and k == 0: rr(lx0, y, lw, rh, 28, SEC_CONT, d)
                hit(lx0, y, lw, rh, 0x300 + k, d)
                ax = lx0 + 16; tx = ax + 56
                rr(ax, y + (rh - 40) // 2, 40, 40, 20, SEC_CONT if not (cls == 2 and k == 0) else SURFACE, d)
                icon(ax + 8, y + (rh - 40) // 2 + 8, 24, ON_SURF, 'person', 'filled', d)
                blk = lh(BODY) + 4 + lh(SMALL); top = y + (rh - blk) // 2
                assert tx - lx0 + max(tw(BODY, hl), tw(SMALL, sub)) <= lw - 16, ('message row text does not fit', sc, cls, hl, sub)
                T(tx, top + asc(BODY), BODY, ON_SURF, hl, d); T(tx, top + lh(BODY) + 4 + asc(SMALL), SMALL, ON_VAR, sub, d)
            fabx, faby = (W - 72, 568) if cls == 0 else ((W - 72, 648) if cls == 1 else (384, 648))
            rr(fabx, faby, 56, 56, 16, PRIMARY_CONT, d); hit(fabx, faby, 56, 56, 0x400, d); icon(fabx + 16, faby + 16, 24, ON_PC, 'add', 'filled', d)
            if cls == 2:                                                 # the detail pane (canonical list-detail): the selected conversation
                dw = W - 472 - 16; rr(472, 96, dw, 480, 28, CONT, d)
                assert 24 + tw(TITLE, "Weekly plan") <= dw - 24 and 24 + tw(SMALL, "No pending items") <= dw - 24 and 24 + tw(BODY, "Nothing to do this week.") <= dw - 24
                T(496, 96 + 24 + asc(TITLE), TITLE, ON_SURF, "Weekly plan", d); T(496, 96 + 24 + lh(TITLE) + 8 + asc(SMALL), SMALL, ON_VAR, "No pending items", d)
                T(496, 96 + 24 + lh(TITLE) + 8 + lh(SMALL) + 24 + asc(BODY), BODY, ON_SURF, "Nothing to do this week.", d)
        else:                                                            # FIND: a Material 3 SEARCH BAR (56 dp, extra-large shape) under the status bar, the recent searches below it
            fw_ = W - fx - 16
            rr(fx, 32, fw_, 56, 28, CONT, d); hit(fx, 32, fw_, 56, 0x310, d); icon(fx + 16, 48, 24, ON_VAR, 'search', 'filled', d)
            T(fx + 56, base(32, 56, BODY), BODY, ON_VAR, "Search messages", d | EMPTY); assert 56 + tw(BODY, "Search messages") <= fw_ - 16, ('placeholder', sc)
            rh = max(56, -(-(lh(BODY) + 16) // 8) * 8)
            for k, t_ in enumerate(("weekly plan", "receipts", "travel")):
                y = 104 + k * rh; hit(fx, y, fw_, rh, 0x320 + k, d); icon(fx + 16, y + (rh - 24) // 2, 24, ON_VAR, 'history', 'outlined', d); T(fx + 56, base(y, rh, BODY), BODY, ON_SURF, t_, d)
    app(HOME_); app(FIND_)
    # ---- launcher (the home screen): wallpaper, the hotseat, the search pill ----
    d = LAUNCH_; rect(0, 0, W, HT, WALL, d); status(d)
    for k, (lab, gl, act) in enumerate((("Messages", 'chat', 0x510), ("Search", 'search', 0x511), ("Settings", 'settings', 0x512))):
        xc = W // 2 + (k - 1) * 112; ix = xc - 32; iy = 536; tw_ = tw(LABEL, lab); assert tw_ <= 104, ('app label', sc, lab)
        rr(ix, iy, 64, 64, 28, PRIMARY_CONT, d); icon(ix + 16, iy + 16, 32, ON_PC, gl, 'filled', d); T(xc - tw_ // 2, base(iy + 68, 28, LABEL), LABEL, WHITE, lab, d)
        hit(xc - 48, iy - 8, 96, 112, act, d)
    pw = min(W - 32, 480); px = (W - pw) // 2
    rr(px, 648, pw, 56, 28, CONT, d); hit(px, 648, pw, 56, 0x513, d); icon(px + 16, 664, 24, ON_VAR, 'search', 'filled', d); T(px + 56, base(648, 56, BODY), BODY, ON_VAR, "Search", d)
    # ---- recents: the one Messages task as a card, "Clear all" ----
    d = RECENTS_; rect(0, 0, W, HT, CONT, d); status(d)
    cw, ch_ = (264 if cls == 0 else 360), 480; cx0, cy0 = (W - cw) // 2, 96
    rr(cx0, cy0, cw, ch_, 28, SURFACE, d | TASK); hit(cx0, cy0, cw, ch_, 0x520, d | TASK)
    rr(cx0 + 16, cy0 + 16, 40, 40, 20, PRIMARY_CONT, d | TASK); icon(cx0 + 24, cy0 + 24, 24, ON_PC, 'chat', 'filled', d | TASK)
    assert 16 + 40 + 12 + tw(BODY, "Messages") <= cw - 16 and lh(BODY) <= 40, ('recents header', sc); T(cx0 + 68, base(cy0 + 16, 40, BODY), BODY, ON_SURF, "Messages", d | TASK)
    rr(cx0 + 16, cy0 + 72, cw - 32, ch_ - 88, 12, CONT, d | TASK)
    for k in range(3): rr(cx0 + 32, cy0 + 88 + 56 * k, cw - 64, 40, 8, SEC_CONT, d | TASK)
    cl = -(-(tw(LABEL, "Clear all") + 48) // 8) * 8; clx = (W - cl) // 2; assert lh(LABEL) <= 48
    rr(clx, 600, cl, 48, 24, SEC_CONT, d | TASK); hit(clx, 600, cl, 48, 0x521, d | TASK); T(clx + (cl - tw(LABEL, "Clear all")) // 2, base(600, 48, LABEL), LABEL, ON_SURF, "Clear all", d | TASK)
    nt = "No recent items"; T((W - tw(BODY, nt)) // 2, base(344, 48, BODY), BODY, ON_VAR, nt, d | NOTASK)
    # ---- quick settings: the shade covers the window (not the navigation bar) ----
    d = SHADE_; rect(0, 0, W, NAVY, CONT, d); status(d)
    T(24, base(24, 44, TITLE), TITLE, ON_SURF, "Quick settings", d); assert 24 + lh(TITLE) <= 72
    for k, (lab, act, gl) in enumerate((("Dark", 0x500, 'dark_mode'), ("Text", 0x503, 'format_size'), ("Home", 0x502, 'home'))):
        tx = 16 + 112 * k; assert 40 + tw(LABEL, lab) <= 104 - 8 and lh(LABEL) <= 48, ('tile label', sc, lab)
        rr(tx, 72, 104, 56, 28, SEC_CONT, d); hit(tx, 72, 104, 56, act, d); icon(tx + 10, 88, 24, ON_SURF, gl, 'outlined', d); T(tx + 40, base(72, 56, LABEL), LABEL, ON_SURF, lab, d)
    assert 40 + tw(LABEL, "Nav") <= 104 - 8 and lh(LABEL) <= 48
    rr(16, 136, 104, 56, 28, SEC_CONT, d); hit(16, 136, 104, 56, 0x509, d); icon(26, 152, 24, ON_SURF, 'swipe_up', 'outlined', d); T(56, base(136, 56, LABEL), LABEL, ON_SURF, "Nav", d)      # the navigation mode tile: gestures <-> 3 buttons
    C = d | NOTIF; assert 48 + tw(BODY, "Welcome") <= W - 32 - 48 - 16 and lh(BODY) <= 56
    rr(16, 216, W - 32, 56, 12, SURFACE, C); hit(16, 216, W - 32 - 48, 56, 0x504, C); hit(W - 64, 220, 48, 48, 0x505, C)
    icon(28, 232, 24, ON_SURF, 'notifications', 'outlined', C); T(64, base(216, 56, BODY), BODY, ON_SURF, "Welcome", C); icon(W - 52, 232, 24, ON_VAR, 'close', 'outlined', C)
    # ---- the system NAVIGATION BAR (3-button mode: Back, Home, Recents; gesture mode: the pill), always visible, drawn last so it is on top of everything ----
    for d in (HOME_, FIND_, SHADE_, LAUNCH_, RECENTS_):
        if d != LAUNCH_: rect(0, NAVY, W, NAVH, BG[d], d)
        for i, (gl, act) in enumerate((('arrow_left', 0x508), ('radio_button_unchecked', 0x506), ('crop_square', 0x507))):
            bx = (W - 288) // 2 + 96 * i; hit(bx, NAVY, 96, NAVH, act, d | BTN); icon(bx + 32, NAVY + 8, 32, FG[d], gl, 'filled', d | BTN)
        rr((W - 108) // 2, 756, 108, 4, 2, FGV[d], d | GST)                # gesture navigation: just the pill (swipe up = Home, swipe up and hold = Recents, edge swipes = Back)
    return L

out = ["/* GENERATED by tools/make_shell_layout.py -- do not edit. */", "shell_base:"]
strs = {}
def sl(t):
    if t not in strs:
        base_ = "s_" + re.sub(r"[^a-z0-9]+", "_", t.lower()).strip("_"); lab = base_; n_ = 1
        while lab in strs.values(): n_ += 1; lab = "%s_%d" % (base_, n_)
        strs[t] = lab
    return strs[t]
def emit(label, lst):
    out.append(label + ":")
    for (t, x, y, w, h, r, col, txt, ax) in lst:
        assert (r in RADII + [0] and 2 * r <= min(w, h)) if t & 0xff == 2 else True
        aux = "%s - shell_base" % sl(txt) if txt else str(ax)
        out.append("    .long %d, %d, %d, %d, %d, %d, 0x%06X, %s" % (t, x, y, w, h, r, col, aux))
    out.append("    .long 0, 0, 0, 0, 0, 0, 0, 0")
for k_, (sc_, c, W) in enumerate((sc_, c_, W_) for sc_ in range(3) for c_, W_ in enumerate((360, 640, 1024))):     # list index = 3 * font scale + width class
    emit("dl_c%d" % k_, layout(W, sc_))
for k_, (sc_, c, W) in enumerate((sc_, c_, W_) for sc_ in range(3) for c_, W_ in enumerate((360, 640, 1024))):     # 1-51: the full-screen (tall) lists, list index = 9 + 3 * font scale + width class
    emit("dl_t%d" % k_, layout_tall(W, sc_))
for t, l in strs.items(): out.append('%s: .asciz "%s"' % (l, t))
tab, off = bytearray(), {}
for r in RADII:
    off[r] = len(tab)
    for k in range(r):
        dy2 = 2 * (r - k) - 1; tab.append(r - (math.isqrt(4 * r * r - dy2 * dy2) + 1) // 2)
DARK = [(SURFACE, 0x141218), (CONT, 0x211F26), (PRIMARY_CONT, 0x4F378B), (SEC_CONT, 0x4A4458), (ON_SURF, 0xE6E0E9), (ON_VAR, 0xCAC4D0), (ON_PC, 0xEADDFF), (0x6750A4, 0xD0BCFF)]
out.append("ovl_tab:   /* list index (9 * tall + 3 * font scale + width class) x 16 dwords: face of the typed text (body-large), BASELINE, caret top, caret height, field x, field y, field width = W - this, text-fit limit = W - this, text offset in the field, cover flag (1: the overlay repaints the field -- the band layouts), 6 spare */")
for tall_ in (0, 1):
    for sc_ in range(3):
        for c_ in range(3):
            f_ = 4 * sc_ + BODY; asc_, desc_ = GLYPH_HEADS[f_]
            if not tall_: fx_, fy_, fws_, lim_, tdx_, cov_ = 96, 96, 112, 148, 24, 1
            else: fx_ = 16 if c_ == 0 else 96; fy_, fws_, lim_, tdx_, cov_ = 32, fx_ + 16, fx_ + 88, 56, 0
            bl_ = fy_ + (56 - asc_ - desc_) // 2 + asc_
            out.append("    .long %d, %d, %d, %d, %d, %d, %d, %d, %d, %d, 0, 0, 0, 0, 0, 0" % (f_, bl_, bl_ - asc_, asc_ + desc_, fx_, fy_, fws_, lim_, tdx_, cov_))
out.append("dk_tab:"); out += ["    .long 0x%06X, 0x%06X" % pr for pr in DARK]; out.append("    .long 0, 0")   # light -> dark colour roles (Material 3 baseline dark scheme)
out.append("rr_off:"); out.append("    .long " + ", ".join(str(off.get(r, 0)) for r in range(0, 29)))
out.append("rr_tab:"); out.append("    .byte " + ", ".join(str(b) for b in tab))
out.append("/* font face asc desc: " + " | ".join("%d %dpx w%d asc %d desc %d" % (k, px, wt, GLYPH_HEADS[k][0], GLYPH_HEADS[k][1]) for k, (wt, px) in enumerate(fontgen.FACES)) + " */")
out.append("font_hdr:"); out += ["    .long %d, %d" % hd for hd in GLYPH_HEADS]
out.append("font_gl:   /* 12 faces x 95 glyphs (ASCII 32..126) x 3 dwords: mask offset | w | h | bx | top (signed bytes) | advance */")
for gl in GLYPH_TABLE:
    for (off, w, h, bx, top, adv) in gl: out.append("    .long %d, 0x%08X, %d" % (off, (w & 255) | (h & 255) << 8 | (bx & 255) << 16 | (top & 255) << 24, adv))
out.append("font_masks:   /* %d bytes: alpha masks of Roboto (SIL OFL-1.1, third_party/roboto) rasterized by tools/fontgen.py */" % len(GLYPH_MASKS))
for k in range(0, len(GLYPH_MASKS), 32): out.append("    .byte " + ", ".join(str(b) for b in GLYPH_MASKS[k:k + 32]))
for (nm, var, sz, rot), off in ICONS.items(): out.append("/* icon %s %s %d %d @ %d */" % (var, nm, sz, rot, off))   # manifest read by tools/check_icons.py
out.append("icons:   /* %d bytes: alpha masks of the official Material icons (third_party/material-icons, Apache-2.0), rasterized by tools/svgraster.py */" % len(ICON_TAB))
for k in range(0, len(ICON_TAB), 32): out.append("    .byte " + ", ".join(str(b) for b in ICON_TAB[k:k + 32]))
open(sys.argv[1] if len(sys.argv) > 1 else "userprog/shell_layout.inc", "w").write("\n".join(out) + "\n")
