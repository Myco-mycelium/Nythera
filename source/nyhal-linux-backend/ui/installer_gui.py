"""
Nyrqis OS — Live Installer GUI Renderer

Renders every screen of the live installer (nyrqis_live_install.py) as a
high-resolution PNG using the same PIL pipeline as the Nyrqis desktop
preview. Also produces an animated boot-splash GIF (scrolling kernel
messages + filling progress bar) and an animated installation GIF.

Usage:
    python3 ui/installer_gui.py                     # PNGs into /tmp/nyrqis_gui
    python3 ui/installer_gui.py --out ./shots       # custom output dir
    python3 ui/installer_gui.py --no-gifs           # PNGs only
    python3 ui/installer_gui.py --gif boot          # boot GIF only

Or programmatically:
    from ui.installer_gui import InstallerGui
    gui = InstallerGui(1280, 720)
    gui.render_all("/tmp/nyrqis_gui")
"""

from __future__ import annotations

import os
import sys
import time
from typing import List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None
    ImageDraw = None

import nyrqis_live_install as tui


# ---------------------------------------------------------------------------
# Colors (consistent with ui/desktop_preview.py)
# ---------------------------------------------------------------------------
WALLPAPER_TOP = (25, 25, 45)
WALLPAPER_BOT = (15, 15, 35)
WINDOW_BG = (28, 28, 48)
WINDOW_TITLE = (35, 35, 55)
PANEL_BG = (33, 33, 54)
PANEL_BG_SEL = (80, 180, 255)
PANEL_EDGE = (55, 55, 80)
WHITE = (220, 220, 230)
GRAY = (100, 100, 120)
DARK_TEXT = (16, 18, 28)
GREEN = (60, 200, 100)
ACCENT = (80, 180, 255)
AMBER = (240, 180, 60)
RED = (235, 90, 90)
BOOT_BG = (8, 8, 14)
LOG_GREEN = (130, 220, 150)


def _get_font(size: int, mono: bool = True):
    """Get a truetype font, matching desktop_preview conventions."""
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    ] if mono else [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for p in paths:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


# Wizard steps shown in the sidebar
WIZARD_STEPS: List[Tuple[tui.InstallerStep, str]] = [
    (tui.InstallerStep.WELCOME, "Welcome"),
    (tui.InstallerStep.LANGUAGE, "Language"),
    (tui.InstallerStep.KEYBOARD, "Keyboard"),
    (tui.InstallerStep.TIMEZONE, "Timezone"),
    (tui.InstallerStep.DISK_SELECT, "Installation disk"),
    (tui.InstallerStep.PARTITIONING, "Partitioning"),
    (tui.InstallerStep.USER_SETUP, "User account"),
    (tui.InstallerStep.NETWORK, "Network"),
    (tui.InstallerStep.PACKAGES, "Packages"),
    (tui.InstallerStep.BOOTLOADER, "Bootloader"),
    (tui.InstallerStep.SUMMARY, "Summary"),
    (tui.InstallerStep.INSTALLING, "Install"),
    (tui.InstallerStep.COMPLETE, "Complete"),
]


def _default_nav() -> "tui.NavState":
    """Build a NavState pre-filled the way the autopilot demo ends up."""
    nav = tui.NavState()
    nav.current_step = tui.InstallerStep.WELCOME
    nav.lang_index = 0
    nav.kb_index = 0
    nav.tz_index = 0
    nav.selected_disk = 0
    nav.selected_layout_idx = 0
    nav.selected_pkg_indices = {0, 1, 2, 3, 7, 8, 9}
    nav.bl_type = tui.BLTYPE.SYSTEMD_BOOT
    nav.boot_timeout = 5
    nav.user = tui.User(username="demo", password="demo1234",
                        hostname="nyrqis-demo", auto_login=True)
    nav.progress = 100.0
    return nav


class InstallerGui:
    """PIL-based renderer for the Nyrqis live installer."""

    SIDEBAR_W = 260
    PAD = 24

    def __init__(self, width: int = 1280, height: int = 720):
        if Image is None:
            raise RuntimeError("Pillow is required for GUI rendering "
                               "(pip install Pillow)")
        self.width = width
        self.height = height
        self.image = Image.new("RGB", (width, height), BOOT_BG)
        self.d = ImageDraw.Draw(self.image)
        self._f = {s: _get_font(s) for s in (10, 11, 12, 13, 14, 16, 18, 20, 26, 34, 40, 46)}
        self._fs = {s: _get_font(s, mono=False) for s in (10, 12, 13, 14, 16, 20, 26, 40, 46)}
        _load_kernel_messages()

    # -- low-level helpers ---------------------------------------------------

    def _t(self, xy, s, size=14, fill=WHITE, sans=False):
        self.d.text(xy, s, font=self._fs[size] if sans else self._f[size],
                    fill=fill)

    def _tw(self, s, size=14, sans=False) -> float:
        f = self._fs[size] if sans else self._f[size]
        return self.d.textlength(s, font=f)

    def _rrect(self, box, radius=8, fill=None, outline=None, width=1):
        self.d.rounded_rectangle(box, radius=radius, fill=fill,
                                 outline=outline, width=width)

    def _logo(self, cx: int, cy: int, scale: float = 1.0):
        """Draw the Nyrqis mushroom logo centred at (cx, cy)."""
        r = int(60 * scale)
        cap_h = int(80 * scale)
        stem_w = int(30 * scale)
        stem_h = int(50 * scale)
        spot_r = max(2, int(10 * scale))
        self.d.ellipse([cx - r, cy - r, cx + r, cy + r - cap_h + r],
                       fill=ACCENT)
        self._rrect([cx - stem_w, cy - cap_h + r - 12,
                     cx + stem_w, cy + stem_h],
                    radius=int(10 * scale), fill=ACCENT)
        for sx, sy in ((-0.25, -0.55), (0.25, -0.55), (0.0, -0.85),
                       (-0.55, -0.2), (0.55, -0.2)):
            ex = cx + int(r * sx)
            ey = cy + int(r * sy)
            self.d.ellipse([ex - spot_r, ey - spot_r, ex + spot_r, ey + spot_r],
                           fill=BOOT_BG)

    def _progress_bar(self, x: int, y: int, w: int, h: int, frac: float,
                      fill=GREEN, bg=(40, 40, 55)):
        self._rrect([x, y, x + w, y + h], radius=h // 2, fill=bg)
        fw = max(h, int(w * max(0.0, min(1.0, frac))))
        if frac > 0:
            self._rrect([x, y, x + fw, y + h], radius=h // 2, fill=fill)

    # -- frame chrome ----------------------------------------------------------

    def _frame(self, step: tui.InstallerStep, title: str, subtitle: str
               ) -> Tuple[int, int]:
        """Draw wallpaper + window + sidebar; return content origin (x, y)."""
        d = self.d
        # Wallpaper gradient
        for y in range(self.height):
            t = y / self.height
            c = tuple(int(WALLPAPER_TOP[i] + (WALLPAPER_BOT[i] - WALLPAPER_TOP[i]) * t)
                      for i in range(3))
            d.line([(0, y), (self.width, y)], fill=c)
        # Title bar dots
        for i, c in enumerate((RED, AMBER, GREEN)):
            d.ellipse([18 + i * 22, 14, 30 + i * 22, 26], fill=c)
        self._t((self.width // 2 - 150, 12), "Nyrqis OS Installer  —  0.14.39",
                12, GRAY)

        # Window
        wx, wy = 14, 40
        ww, wh = self.width - 28, self.height - 54
        self._rrect([wx, wy, wx + ww, wy + wh], radius=14,
                    fill=WINDOW_BG, outline=PANEL_EDGE, width=2)
        self.d.rectangle([wx, wy, wx + ww, wy + 52], fill=WINDOW_TITLE)
        self._t((wx + 20, wy + 16), title, 20, WHITE, sans=True)
        self._t((wx + 22, wy + 36), subtitle, 12, GRAY)

        # Sidebar
        sx = wx + 16
        sy = wy + 66
        sw = self.SIDEBAR_W
        active_idx = next((i for i, (s, _) in enumerate(WIZARD_STEPS)
                           if s == step), -1)
        row_h = 34
        d.rounded_rectangle([sx, sy, sx + sw, sy + len(WIZARD_STEPS) * row_h + 16],
                            radius=10, fill=(24, 24, 42))
        self._t((sx + 14, sy + 8), "INSTALLATION STEPS", 10, GRAY)
        for i, (s, label) in enumerate(WIZARD_STEPS):
            yy = sy + 26 + i * row_h
            done = active_idx > i or (step == tui.InstallerStep.COMPLETE
                                      and s != tui.InstallerStep.COMPLETE)
            if i == active_idx:
                self._rrect([sx + 6, yy, sx + sw - 6, yy + row_h - 4],
                            radius=8, fill=PANEL_BG_SEL)
                col, dot, dotc = DARK_TEXT, "●", DARK_TEXT
            elif done:
                col, dot, dotc = GRAY, "✓", GREEN
            else:
                col, dot, dotc = GRAY, "○", GRAY
            self._t((sx + 16, yy + 8), dot, 13, dotc)
            self._t((sx + 36, yy + 8), label, 13, col)
        cx = sx + sw + 16
        return cx, wy + 66

    def _panel(self, x, y, w, h, title: Optional[str] = None,
               selected=False, fill=PANEL_BG):
        self._rrect([x, y, x + w, y + h], radius=10,
                    fill=fill if not selected else PANEL_BG_SEL,
                    outline=PANEL_BG_SEL if selected else PANEL_EDGE, width=2)
        if title:
            self._t((x + 14, y + 10), title, 13,
                    DARK_TEXT if selected else WHITE, sans=True)

    def _selectable_row(self, x, y, w, h, label: str, selected: bool,
                        note: str = "", tag: str = "", tag_col=None):
        self._rrect([x, y, x + w, y + h], radius=8,
                    fill=PANEL_BG_SEL if selected else PANEL_BG,
                    outline=PANEL_BG_SEL if selected else PANEL_EDGE, width=1)
        col = DARK_TEXT if selected else WHITE
        self._t((x + 14, y + h // 2 - 9), label, 14, col)
        if tag:
            self._t((x + w - 14 - self._tw(tag, 12), y + h // 2 - 8), tag, 12,
                    tag_col or col)
        if note:
            self._t((x + 320, y + h // 2 - 8), note, 12,
                    DARK_TEXT if selected else GRAY)

    def _save(self, path: str) -> str:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.image.save(path)
        return path

    # -- full-screen (no chrome) renders --------------------------------------

    def render_boot_splash(self, progress: float = 0.35,
                           msg_count: int = 18) -> Image.Image:
        d = self.d
        d.rectangle([0, 0, self.width, self.height], fill=BOOT_BG)
        cx, cy = self.width // 2, int(self.height * 0.30)
        self._logo(cx, cy, scale=1.1)
        self._t((cx - self._tw("NYRQIS", 46, True) / 2, cy + 70), "NYRQIS",
                46, WHITE, sans=True)
        self._t((cx - self._tw("Operating System 0.14.39", 14) / 2, cy + 130),
                "Operating System 0.14.39", 14, GRAY)
        self._progress_bar(cx - 150, cy + 170, 300, 8, progress)
        # Scrolling kernel messages
        msgs = BootSplashMessages.MESSAGES[:msg_count]
        y = self.height - 24 - len(msgs) * 16
        for i, m in enumerate(msgs):
            d.text((24, y + i * 16), m, font=self._f[10],
                   fill=LOG_GREEN if i % 2 == 0 else (90, 160, 105))
        return self.image

    # -- wizard screens ----------------------------------------------------------

    def render_iso_select(self, nav=None) -> Image.Image:
        nav = nav or _default_nav()
        title = "Select Installation Media"
        sub = "Choose the ISO to install from"
        cx, cy = self._frame(tui.InstallerStep.WELCOME, title, sub)
        rows_w = 420
        for i, iso in enumerate(tui.ISO_MEDIA):
            y = cy + i * 56
            sel = iso["status"] == "inserted"
            tag = iso["size"]
            col = GREEN if sel else (GRAY if iso["status"] != "empty" else (70, 70, 90))
            self._selectable_row(cx, y, rows_w, 48, iso["label"], sel,
                                 tag=tag, tag_col=col)
        self._t((cx, cy + len(tui.ISO_MEDIA) * 56 + 12),
                "The media must be inserted before continuing.", 12, GRAY)
        # Details panel
        dx = cx + rows_w + 20
        dw = self.width - 14 - dx - 16
        dh = len(tui.ISO_MEDIA) * 56 + 8
        self._panel(dx, cy, dw, dh, "Media Details", selected=True)
        d0 = tui.ISO_MEDIA[0]
        lines = [
            ("Label", d0["label"]), ("Size", d0["size"]),
            ("Type", d0["type"]), ("Contents", d0["contents"]),
            ("Status", "Inserted"), ("Signature", "verified"),
            ("Publisher", "Nyrqis Project"),
        ]
        for i, (k, v) in enumerate(lines):
            yy = cy + 40 + i * 26
            self._t((dx + 16, yy), f"{k:<10}", 12, GRAY)
            self._t((dx + 110, yy), str(v)[:dw // 7], 12, GREEN)
        self._t((dx + 16, cy + dh - 34), "Media ready for install.", 12, GREEN)
        return self.image

    def render_welcome(self, nav=None) -> Image.Image:
        nav = nav or _default_nav()
        cx, cy = self._frame(tui.InstallerStep.WELCOME,
                             "Welcome to Nyrqis OS",
                             "This installer will set up Nyrqis on your computer")
        self._logo(cx + 70, cy + 90, scale=0.8)
        body = [
            "Nyrqis OS 0.14.39 brings the NyHAL kernel backend, the NUI",
            "desktop shell, and the NyVault key manager to your machine.",
            "",
            "The installer will help you with:",
            "  •  Choosing installation media and target disk",
            "  •  Partitioning and filesystem setup",
            "  •  Creating your user account",
            "  •  Selecting software packages",
            "  •  Installing the bootloader",
        ]
        bx = cx + 180
        for i, line in enumerate(body):
            self._t((bx, cy + 20 + i * 26), line, 13,
                    WHITE if not line.startswith("  •") else ACCENT)
        # Requirements checklist
        rx, ry = cx, cy + 240
        rw = self.width - 14 - 16 - rx
        self._panel(rx, ry, rw, 130, "System Requirements")
        reqs = [("64-bit CPU", True), ("4 GB RAM (8 GB recommended)", True),
                ("25 GB free disk space", True), ("UEFI firmware", True),
                ("Internet connection (optional)", False)]
        for i, (label, ok) in enumerate(reqs):
            col_x = rx + 20 + (i % 2) * (rw // 2)
            row_y = ry + 40 + (i // 2) * 26
            mark = "✓" if ok else "○"
            self._t((col_x, row_y), mark, 13, GREEN if ok else GRAY)
            self._t((col_x + 20, row_y), label, 13, WHITE if ok else GRAY)
        # Continue button
        bw = 220
        bx2 = self.width - 14 - 16 - bw
        self._rrect([bx2, ry + 150, bx2 + bw, ry + 192], radius=10, fill=GREEN)
        self._t((bx2 + 60, ry + 163), "Continue →", 14, DARK_TEXT, sans=True)
        return self.image

    def _list_screen(self, step, title, sub, items, selected_idx, note=None,
                     detail_lines=None, two_cols=True) -> Image.Image:
        cx, cy = self._frame(step, title, sub)
        rows = [(n, c) for n, c in items]
        col_w = 420 if two_cols else self.width - 14 - 16 - cx
        per_col = (len(rows) + 1) // 2 if two_cols else len(rows)
        row_h = 40
        for i, (name, code) in enumerate(rows):
            col = i // per_col if two_cols else 0
            row = i % per_col if two_cols else i
            x = cx + col * (col_w + 16)
            y = cy + row * (row_h + 8)
            sel = i == selected_idx
            self._selectable_row(x, y, col_w, row_h, name, sel,
                                 tag=code, tag_col=GREEN if sel else GRAY)
        if note:
            self._t((cx, cy + (per_col + 1) * (row_h + 8) + 8), note, 12, GRAY)
        if detail_lines:
            dx = cx + 2 * (col_w + 16) + 8 if two_cols else cx
            dw = self.width - 14 - 16 - dx
            self._panel(dx, cy, dw, per_col * (row_h + 8) + 8, "Details")
            for i, line in enumerate(detail_lines):
                self._t((dx + 16, cy + 40 + i * 24), line[:dw // 7], 12, WHITE)
        return self.image

    def render_language(self, nav) -> Image.Image:
        lang = tui._LANGUAGES[nav.lang_index]
        return self._list_screen(
            tui.InstallerStep.LANGUAGE, "Choose Your Language",
            "Select the display language for Nyrqis OS",
            tui._LANGUAGES, nav.lang_index,
            note=f"Selected: {lang[0]} ({lang[1]})")

    def render_keyboard(self, nav) -> Image.Image:
        kb = tui._KEYBOARDS[nav.kb_index]
        return self._list_screen(
            tui.InstallerStep.KEYBOARD, "Keyboard Layout",
            "Select your keyboard layout",
            tui._KEYBOARDS, nav.kb_index,
            note=f"Layout: {kb[0]} ({kb[1]}) — test here: "
                 f"\"the quick brown fox\"")

    def render_timezone(self, nav) -> Image.Image:
        tz = tui._TIMEZONES[nav.tz_index]
        now = time.strftime("%H:%M")
        return self._list_screen(
            tui.InstallerStep.TIMEZONE, "Select Your Location",
            "Choose your timezone — used for clock and updates",
            tui._TIMEZONES, nav.tz_index,
            note=f"Timezone: {tz[0]} ({tz[1]})   Local time: {now}")

    def render_disk_select(self, nav) -> Image.Image:
        cx, cy = self._frame(tui.InstallerStep.DISK_SELECT,
                             "Select Installation Disk",
                             "Choose where Nyrqis OS will be installed")
        rows_w = 480
        for i, disk in enumerate(tui.SAMPLE_DISKS):
            y = cy + i * 62
            sel = i == nav.selected_disk
            self._rrect([cx, y, cx + rows_w, y + 54], radius=8,
                        fill=PANEL_BG_SEL if sel else PANEL_BG,
                        outline=PANEL_BG_SEL if sel else PANEL_EDGE, width=2)
            col = DARK_TEXT if sel else WHITE
            self._t((cx + 16, y + 10), f"{disk.device}  —  {disk.model}", 14, col)
            note = f"{disk.interface} · {disk.type_label} · {disk.size_gb} GB"
            if disk.removable:
                note += " · USB"
            self._t((cx + 16, y + 32), note, 12,
                    DARK_TEXT if sel else GRAY)
            size_tag = f"{disk.size_gb} GB"
            self._t((cx + rows_w - 20 - self._tw(size_tag, 13), y + 10),
                    size_tag, 13, DARK_TEXT if sel else ACCENT)
        # Details panel
        dx = cx + rows_w + 20
        dw = self.width - 14 - 16 - dx
        d = tui.SAMPLE_DISKS[nav.selected_disk]
        self._panel(dx, cy, dw, 4 * 62 + 8, "Disk Details")
        infos = [("Device", d.device), ("Model", d.model),
                 ("Size", f"{d.size_gb} GB"), ("Interface", d.interface),
                 ("Partitions", str(d.partitions or 1))]
        for i, (k, v) in enumerate(infos):
            self._t((dx + 16, cy + 40 + i * 26), f"{k:<10}", 12, GRAY)
            self._t((dx + 110, cy + 40 + i * 26), str(v)[:dw // 7], 12, WHITE)
        self._t((dx + 16, cy + 4 * 62 - 18),
                "Warning: all data on this disk will be erased.", 12, AMBER)
        return self.image

    def render_partitioning(self, nav) -> Image.Image:
        cx, cy = self._frame(tui.InstallerStep.PARTITIONING,
                             "Partitioning",
                             "How should the disk be laid out?")
        rows_w = 460
        for i, layout in enumerate(tui.SAMPLE_LAYOUTS):
            y = cy + i * 66
            sel = i == nav.selected_layout_idx
            self._rrect([cx, y, cx + rows_w, y + 58], radius=8,
                        fill=PANEL_BG_SEL if sel else PANEL_BG,
                        outline=PANEL_BG_SEL if sel else PANEL_EDGE, width=2)
            col = DARK_TEXT if sel else WHITE
            self._t((cx + 14, y + 10), layout.name, 14, col)
            self._t((cx + 14, y + 32), layout.desc, 12,
                    DARK_TEXT if sel else GRAY)
            if layout.recommended:
                self._t((cx + rows_w - 110, y + 12), "RECOMMENDED", 10,
                        DARK_TEXT if sel else GREEN)
        # Partition table
        dx = cx + rows_w + 20
        dw = self.width - 14 - 16 - dx
        layout = tui.SAMPLE_LAYOUTS[nav.selected_layout_idx]
        ph = 66 + len(tui.SAMPLE_LAYOUTS) * 66 - 58
        self._panel(dx, cy, dw, ph, "Partition Layout")
        if layout.partitions:
            hdr_y = cy + 38
            for j, htxt in enumerate(("Mount", "FS", "Size")):
                self._t((dx + 16 + j * 110, hdr_y), htxt, 12, GRAY)
            for i, p in enumerate(layout.partitions):
                yy = hdr_y + 24 + i * 30
                self._t((dx + 16, yy), p.mount, 12, ACCENT)
                self._t((dx + 126, yy), p.fs.value, 12, WHITE)
                self._t((dx + 236, yy), f"{p.size_gb:.1f} GB", 12, WHITE)
        else:
            self._t((dx + 16, cy + 60), "You will configure partitions", 12, GRAY)
            self._t((dx + 16, cy + 82), "manually in the next step.", 12, GRAY)
        return self.image

    def render_user_setup(self, nav) -> Image.Image:
        cx, cy = self._frame(tui.InstallerStep.USER_SETUP,
                             "Create Your Account",
                             "Set up the primary user for this system")
        u = nav.user
        fw = 480
        fields = [("Username", u.username or "demo"),
                  ("Full name", u.full_name or "Nyrqis Demo User"),
                  ("Hostname", u.hostname),
                  ("Password", "•" * len(u.password)),
                  ("Confirm password", "•" * len(u.password))]
        for i, (label, value) in enumerate(fields):
            y = cy + i * 62
            self._t((cx, y + 6), label, 12, GRAY)
            self._rrect([cx + 160, y, cx + 160 + fw, y + 40], radius=8,
                        fill=(22, 22, 38), outline=PANEL_EDGE, width=1)
            self._t((cx + 176, y + 12), value, 13, WHITE)
        # Strength meter
        pw = u.password
        score = min(4, (len(pw) >= 8) + any(c.isdigit() for c in pw)
                    + any(c.isalpha() for c in pw)
                    + any(not c.isalnum() for c in pw))
        labels = ["Weak", "Weak", "Fair", "Good", "Strong"]
        cols = [RED, RED, AMBER, AMBER, GREEN]
        sy = cy + 5 * 62 + 12
        self._t((cx, sy), "Strength:", 12, GRAY)
        for i in range(4):
            seg_col = cols[score] if i < score else (45, 45, 65)
            self._rrect([cx + 90 + i * 80, sy, cx + 160 + i * 80, sy + 10],
                        radius=4, fill=seg_col)
        self._t((cx + 90 + 4 * 80 + 10, sy - 3), labels[score], 12, cols[score])
        # Auto-login toggle + side panel
        ty = sy + 40
        self._rrect([cx, ty, cx + 34, ty + 22], radius=11,
                    fill=GREEN if u.auto_login else (45, 45, 65))
        dot_x = cx + 21 if u.auto_login else cx + 4
        self.d.ellipse([dot_x, ty + 3, dot_x + 16, ty + 19], fill=WHITE)
        self._t((cx + 46, ty + 4), "Log in automatically", 12, WHITE)
        # Tips panel (clamped so it fits narrow render widths)
        dx = min(cx + fw + 200, self.width - 260)
        dw = max(120, self.width - 14 - 16 - dx)
        self._panel(dx, cy, dw, 320, "Account Details")
        tips = [
            ("Username", u.username or "demo"),
            ("Hostname", u.hostname),
            ("Auto-login", "Yes" if u.auto_login else "No"),
            ("Password", "set" if u.password else "not set"),
            "", "Your home folder will be",
            "created at /home/%s." % (u.username or "demo"),
        ]
        for i, line in enumerate(tips):
            if not line:
                continue
            self._t((dx + 16, cy + 40 + i * 26), str(line)[:dw // 7], 12,
                    GRAY if i >= 5 else WHITE)
        return self.image

    def render_network(self, nav) -> Image.Image:
        cx, cy = self._frame(tui.InstallerStep.NETWORK,
                             "Network Configuration",
                             "Configure your network connection")
        conns = [
            ("Ethernet (enp3s0)", "connected", "192.168.1.105",
             "200 Mbps", "Wired connection active"),
            ("Wi-Fi (wlan0)", "available", "—", "—",
             "Nyrqis_Home_5G  →  Not connected"),
            ("Wi-Fi (wlan1)", "available", "—", "—",
             "Guest_Network  →  Not connected"),
        ]
        rows_w = self.width - 14 - 16 - cx
        for i, (name, status, ip, speed, detail) in enumerate(conns):
            y = cy + i * 60
            sel = status == "connected"
            self._rrect([cx, y, cx + rows_w, y + 52], radius=8,
                        fill=PANEL_BG_SEL if sel else PANEL_BG,
                        outline=PANEL_BG_SEL if sel else PANEL_EDGE, width=2)
            col = DARK_TEXT if sel else WHITE
            icon = "✓" if status == "connected" else "○"
            self._t((cx + 16, y + 16), icon, 14, DARK_TEXT if sel else GRAY)
            self._t((cx + 40, y + 10), name, 14, col)
            self._t((cx + 40, y + 30), detail, 12,
                    DARK_TEXT if sel else GRAY)
            self._t((cx + rows_w - 220, y + 10), status, 12,
                    DARK_TEXT if sel else (GREEN if status == "connected" else GRAY))
            self._t((cx + rows_w - 220, y + 30), f"{ip}  ·  {speed}", 12,
                    DARK_TEXT if sel else GRAY)
        fy = cy + 3 * 60 + 16
        self._t((cx, fy), "Online installers can download updates and "
                          "language packs during installation.", 12, GRAY)
        return self.image

    def render_packages(self, nav) -> Image.Image:
        cx, cy = self._frame(tui.InstallerStep.PACKAGES,
                             "Software Selection",
                             "Choose the package groups to install")
        pkgs = list(tui.PKG)
        rows_w = 560
        for i, p in enumerate(pkgs):
            y = cy + i * 44
            checked = i in nav.selected_pkg_indices
            # Checkbox
            self._rrect([cx, y + 8, cx + 24, y + 32], radius=6,
                        fill=GREEN if checked else (22, 22, 38),
                        outline=PANEL_EDGE if not checked else GREEN, width=1)
            if checked:
                self._t((cx + 4, y + 8), "✓", 13, DARK_TEXT)
            self._t((cx + 38, y + 12), p.label, 13, WHITE)
            mb = f"{p.mb} MB"
            self._t((cx + rows_w - 14 - self._tw(mb, 12), y + 12), mb, 12, GRAY)
        # Totals panel
        dx = cx + rows_w + 24
        dw = self.width - 14 - 16 - dx
        sel = sorted(nav.selected_pkg_indices)
        total = sum(pkgs[i].mb for i in sel if i < len(pkgs))
        self._panel(dx, cy, dw, 250, "Selection Summary")
        stats = [("Groups selected", str(len(sel))),
                 ("Total download", f"~{total} MB"),
                 ("Installed size", f"~{int(total * 2.6)} MB"),
                 ("Disk required", f"~{int(total * 3.4 / 1024) + 6} GB"),
                 ("Download time", "~2 min (200 Mbps)")]
        for i, (k, v) in enumerate(stats):
            self._t((dx + 16, cy + 40 + i * 30), k, 12, GRAY)
            self._t((dx + dw - 20 - self._tw(v, 12), cy + 40 + i * 30),
                    v, 12, GREEN)
        self._t((dx, cy + 270), "You can add or remove packages later", 12, GRAY)
        self._t((dx, cy + 290), "with the Packages app.", 12, GRAY)
        return self.image

    def render_bootloader(self, nav) -> Image.Image:
        cx, cy = self._frame(tui.InstallerStep.BOOTLOADER,
                             "Bootloader Installation",
                             "Choose the bootloader for your system")
        opts = list(tui.BLTYPE)
        descs = {
            tui.BLTYPE.GRUB: "Full-featured, best for dual-boot setups",
            tui.BLTYPE.SYSTEMD_BOOT: "Lightweight, UEFI-only, fast boot",
            tui.BLTYPE.REFIND: "Graphical menu, auto-detects other OSes",
            tui.BLTYPE.LIMINE: "Minimal, modern, multi-protocol",
        }
        rows_w = 560
        for i, bl in enumerate(opts):
            y = cy + i * 60
            sel = bl == nav.bl_type
            self._rrect([cx, y, cx + rows_w, y + 52], radius=8,
                        fill=PANEL_BG_SEL if sel else PANEL_BG,
                        outline=PANEL_BG_SEL if sel else PANEL_EDGE, width=2)
            col = DARK_TEXT if sel else WHITE
            self._t((cx + 16, y + 10), bl.value, 14, col)
            self._t((cx + 16, y + 30), descs[bl], 12,
                    DARK_TEXT if sel else GRAY)
        # Settings panel
        dx = cx + rows_w + 24
        dw = self.width - 14 - 16 - dx
        self._panel(dx, cy, dw, 230, "Boot Settings")
        setv = [("Install target", "EFI system partition"),
                ("Boot entry", "Nyrqis OS 0.14.39"),
                ("Timeout", f"{nav.boot_timeout} seconds"),
                ("Secure Boot", "Supported"),
                ("Fallback entry", "Created automatically")]
        for i, (k, v) in enumerate(setv):
            self._t((dx + 16, cy + 40 + i * 30), k, 12, GRAY)
            self._t((dx + dw - 20 - self._tw(v, 12), cy + 40 + i * 30),
                    v, 12, ACCENT)
        return self.image

    def render_summary(self, nav) -> Image.Image:
        cx, cy = self._frame(tui.InstallerStep.SUMMARY,
                             "Review Your Settings",
                             "Confirm everything before installation begins")
        lang = tui._LANGUAGES[nav.lang_index]
        kb = tui._KEYBOARDS[nav.kb_index]
        tz = tui._TIMEZONES[nav.tz_index]
        disk = tui.SAMPLE_DISKS[nav.selected_disk]
        layout = tui.SAMPLE_LAYOUTS[nav.selected_layout_idx]
        pkgs = list(tui.PKG)
        sel = sorted(nav.selected_pkg_indices)
        total = sum(pkgs[i].mb for i in sel if i < len(pkgs))
        left = [
            ("Language", f"{lang[0]} ({lang[1]})"),
            ("Keyboard", f"{kb[0]} ({kb[1]})"),
            ("Timezone", f"{tz[0]} ({tz[1]})"),
            ("Disk", f"{disk.device} — {disk.model}"),
            ("Partitioning", layout.name),
            ("User", f"{nav.user.username}@{nav.user.hostname}"),
            ("Auto-login", "Yes" if nav.user.auto_login else "No"),
        ]
        right = [
            ("Network", "Ethernet — 192.168.1.105"),
            ("Bootloader", f"{nav.bl_type.value} ({nav.boot_timeout}s)"),
            ("Packages", f"{len(sel)} groups (~{total} MB)"),
            ("Media", tui.ISO_MEDIA[0]["label"]),
            ("Disk space", f"~{int(total * 3.4 / 1024) + 6} GB required"),
            ("Mode", "Guided — erase disk"),
            ("Security", "LUKS encryption: off"),
        ]
        col_w = (self.width - 14 - 16 - cx - 24) // 2
        for col, data in ((0, left), (1, right)):
            x = cx + col * (col_w + 24)
            for i, (k, v) in enumerate(data):
                y = cy + i * 44
                self._panel(x, y, col_w, 36)
                self._t((x + 14, y + 10), k, 12, GRAY)
                self._t((x + 140, y + 10), v[:col_w // 7], 12, WHITE)
        # Install button
        by = cy + max(len(left), len(right)) * 44 + 16
        bw = 260
        bx = cx + col_w + 24 + col_w - bw
        self._rrect([bx, by, bx + bw, by + 46], radius=10, fill=GREEN)
        self._t((bx + 58, by + 14), "Install Now →", 14, DARK_TEXT, sans=True)
        return self.image

    INSTALL_STEPS = [
        ("Preparing filesystem", 5), ("Creating EFI partition", 10),
        ("Formatting root (btrfs)", 20), ("Formatting home (btrfs)", 35),
        ("Creating swap area", 42), ("Mounting filesystems", 48),
        ("Installing base system", 55), ("Installing Nykernel 0.14.39", 62),
        ("Installing NyHAL backend", 68), ("Installing Desktop Environment", 75),
        ("Writing system configuration", 82), ("Creating user account", 87),
        ("Installing systemd-boot", 92), ("Generating initramfs", 96),
        ("Cleaning up", 99),
    ]

    def render_installing(self, progress: float = 55.0, elapsed: int = 234,
                          log_lines: Optional[List[str]] = None) -> Image.Image:
        cx, cy = self._frame(tui.InstallerStep.INSTALLING,
                             "Installing Nyrqis OS",
                             "Please do not turn off your computer")
        # Big progress bar
        self._progress_bar(cx, cy + 10, self.width - 14 - 16 - cx, 18,
                           progress / 100.0)
        self._t((cx, cy + 44), f"{progress:.0f}%", 26, WHITE, sans=True)
        self._t((cx + 110, cy + 56), f"Elapsed: {elapsed // 60}m {elapsed % 60}s",
                12, GRAY)
        # Current step
        cur = next((m for m, t in self.INSTALL_STEPS if progress <= t),
                   self.INSTALL_STEPS[-1][0])
        self._t((cx, cy + 100), "▶  " + cur, 14, ACCENT)
        # Step checklist
        list_y = cy + 140
        col_w = (self.width - 14 - 16 - cx) // 2
        for i, (msg, thresh) in enumerate(self.INSTALL_STEPS):
            col = i // ((len(self.INSTALL_STEPS) + 1) // 2)
            row = i % ((len(self.INSTALL_STEPS) + 1) // 2)
            x = cx + col * (col_w + 16)
            y = list_y + row * 30
            done = progress > thresh
            active = not done and progress <= thresh and \
                abs(thresh - progress) < 10
            mark = "✓" if done else ("●" if active else "○")
            mcol = GREEN if done else (ACCENT if active else GRAY)
            self._t((x, y), mark, 12, mcol)
            self._t((x + 20, y), msg, 12, WHITE if done or active else GRAY)
        # Log panel
        lx = cx + 2 * (col_w + 16) + 8
        lw = self.width - 14 - 16 - lx
        if lw > 200:
            self._panel(lx, list_y, lw, 300, "Installation Log")
            lines = log_lines or [
                "mkfs.vfat -F32 /dev/nvme0n1p1 ... done",
                "mkfs.btrfs /dev/nvme0n1p2 ... done",
                "mount /dev/nvme0n1p2 /mnt ... done",
                "pacstrap -K /mnt base nyrqis-base ...",
                "  → installing nyrqis-kernel 0.14.39",
                "  → installing nyhal-backend 2.1.0",
                "genfstab -U /mnt >> /mnt/etc/fstab",
                "arch-chroot /mnt ... configuring",
                "bootctl install --path=/boot ... done",
            ]
            for i, line in enumerate(lines[-11:]):
                self.d.text((lx + 16, list_y + 38 + i * 22), line[:lw // 7],
                            font=self._f[11],
                            fill=LOG_GREEN if i % 2 else (150, 200, 220))
        return self.image

    def render_complete(self, nav=None) -> Image.Image:
        nav = nav or _default_nav()
        cx, cy = self._frame(tui.InstallerStep.COMPLETE,
                             "Installation Complete",
                             "Nyrqis OS has been installed successfully")
        # Big check circle
        gx, gy = cx + 70, cy + 90
        self.d.ellipse([gx - 50, gy - 50, gx + 50, gy + 50], fill=GREEN)
        self._t((gx - 22, gy - 26), "✓", 40, DARK_TEXT, sans=True)
        lines = [
            f"Nyrqis OS 0.14.39 is installed on "
            f"{tui.SAMPLE_DISKS[nav.selected_disk].device}.",
            "",
            f"User:    {nav.user.username}@{nav.user.hostname}",
            f"Bootloader: {nav.bl_type.value}",
            f"Layout:  {tui.SAMPLE_LAYOUTS[nav.selected_layout_idx].name}",
            "",
            "Remove the installation media, then restart",
            "to boot into your new system.",
        ]
        for i, line in enumerate(lines):
            self._t((cx + 170, cy + 30 + i * 28), line, 13,
                    WHITE if line and not line.startswith(("User", "Bootloader",
                                                           "Layout")) else GRAY)
        # Buttons
        by = cy + 290
        bw = 220
        self._rrect([cx, by, cx + bw, by + 46], radius=10, fill=GREEN)
        self._t((cx + 52, by + 14), "Restart Now", 14, DARK_TEXT, sans=True)
        self._rrect([cx + bw + 20, by, cx + bw + 20 + bw, by + 46],
                    radius=10, fill=PANEL_BG, outline=PANEL_EDGE, width=1)
        self._t((cx + bw + 52, by + 14), "Continue testing", 14, WHITE)
        # Next steps
        nx_y = by + 70
        tips = ["1.  Reboot and remove USB media",
                "2.  Log in — auto-login is enabled",
                "3.  Run the Update Manager on first boot",
                "4.  Explore the NUI desktop and app launcher"]
        for i, tip in enumerate(tips):
            self._t((cx, nx_y + i * 26), tip, 12, ACCENT)
        return self.image

    # -- render-all and GIFs ------------------------------------------------

    def render_all(self, output_dir: str = "/tmp/nyrqis_gui",
                   gifs: bool = True) -> List[str]:
        os.makedirs(output_dir, exist_ok=True)
        nav = _default_nav()
        renders = [
            ("00_boot_splash", lambda: self.render_boot_splash(0.35, 18)),
            ("01_iso_select", lambda: self.render_iso_select(nav)),
            ("02_welcome", lambda: self.render_welcome(nav)),
            ("03_language", lambda: self.render_language(nav)),
            ("04_keyboard", lambda: self.render_keyboard(nav)),
            ("05_timezone", lambda: self.render_timezone(nav)),
            ("06_disk_select", lambda: self.render_disk_select(nav)),
            ("07_partitioning", lambda: self.render_partitioning(nav)),
            ("08_user_setup", lambda: self.render_user_setup(nav)),
            ("09_network", lambda: self.render_network(nav)),
            ("10_packages", lambda: self.render_packages(nav)),
            ("11_bootloader", lambda: self.render_bootloader(nav)),
            ("12_summary", lambda: self.render_summary(nav)),
            ("13_installing", lambda: self.render_installing(55.0, 234)),
            ("14_complete", lambda: self.render_complete(nav)),
        ]
        paths = []
        for name, fn in renders:
            self.image = Image.new("RGB", (self.width, self.height), BOOT_BG)
            self.d = ImageDraw.Draw(self.image)
            fn()
            paths.append(self._save(os.path.join(output_dir, name + ".png")))
        if gifs:
            paths.append(self.render_boot_gif(
                os.path.join(output_dir, "boot_splash.gif"), seconds=5.0))
            paths.append(self.render_install_gif(
                os.path.join(output_dir, "installing.gif"), seconds=6.0))
        return paths

    def render_boot_gif(self, path: str, seconds: float = 5.0,
                        fps: int = 12) -> str:
        frames = []
        total = int(seconds * fps)
        msgs = BootSplashMessages.MESSAGES
        for f in range(total):
            self.image = Image.new("RGB", (self.width, self.height), BOOT_BG)
            self.d = ImageDraw.Draw(self.image)
            frac = f / max(1, total - 1)
            msg_count = min(len(msgs), max(4, int(frac * len(msgs))))
            self.render_boot_splash(progress=frac, msg_count=msg_count)
            frames.append(self.image.copy())
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        frames[0].save(path, save_all=True, append_images=frames[1:],
                       duration=int(1000 / fps), loop=0)
        return path

    def render_install_gif(self, path: str, seconds: float = 6.0,
                           fps: int = 12) -> str:
        frames = []
        total = int(seconds * fps)
        for f in range(total):
            frac = f / max(1, total - 1)
            self.image = Image.new("RGB", (self.width, self.height), BOOT_BG)
            self.d = ImageDraw.Draw(self.image)
            progress = 5 + frac * 95
            n_log = max(1, int(frac * 9))
            self.render_installing(progress=progress,
                                   elapsed=int(frac * 420),
                                   log_lines=self._sample_log()[:n_log + 2])
            frames.append(self.image.copy())
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        frames[0].save(path, save_all=True, append_images=frames[1:],
                       duration=int(1000 / fps), loop=0)
        return path

    @staticmethod
    def _sample_log() -> List[str]:
        return [
            "[  0.4s] mkfs.vfat -F32 /dev/nvme0n1p1 ... done",
            "[  1.2s] mkfs.btrfs /dev/nvme0n1p2 ... done",
            "[  1.9s] mkfs.btrfs /dev/nvme0n1p3 ... done",
            "[  2.3s] mkswap /dev/nvme0n1p4 ... done",
            "[  2.8s] mount /dev/nvme0n1p2 /mnt ... done",
            "[  4.1s] pacstrap -K /mnt base nyrqis-base ...",
            "[ 18.6s]   → nyrqis-kernel 0.14.39 installed",
            "[ 24.0s]   → nyhal-backend 2.1.0 installed",
            "[ 31.5s] genfstab -U /mnt >> /mnt/etc/fstab",
            "[ 33.0s] arch-chroot /mnt ... configuring",
            "[ 47.2s] bootctl install --path=/boot ... done",
            "[ 52.8s] mkinitcpio -P ... done",
        ]


class BootSplashMessages:
    """Kernel messages reused from the TUI boot splash screen."""
    MESSAGES: List[str] = []


def _load_kernel_messages() -> None:
    try:
        cls = tui.BootSplashScreen
        BootSplashMessages.MESSAGES = list(cls._KERNEL_MESSAGES)
    except Exception:
        BootSplashMessages.MESSAGES = [
            "[    0.000000] Linux version 6.8.0-nyrqis+",
            "[    0.045678] Memory: 32768M/32768M available",
            "[    0.289012] nvme nvme0: pci function 0000:00:0e.0",
        ]


def run_gui(output_dir: Optional[str] = None, gifs: bool = True,
            width: int = 1280, height: int = 720) -> List[str]:
    """Entry point used by nyrqis_live_install.py --gui"""
    _load_kernel_messages()
    gui = InstallerGui(width, height)
    out = output_dir or "/tmp/nyrqis_gui"
    paths = gui.render_all(out, gifs=gifs)
    print(f"\n  Rendered {len(paths)} files to {out}:")
    for p in paths:
        print(f"    {p}")
    return paths


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        description="Nyrqis OS — Live Installer GUI renderer",
        epilog="""
Examples:
  python3 ui/installer_gui.py                  # PNGs + GIFs to /tmp/nyrqis_gui
  python3 ui/installer_gui.py --out ./shots    # custom output dir
  python3 ui/installer_gui.py --no-gifs        # still PNGs only
  python3 ui/installer_gui.py --size 1920x1080
        """)
    parser.add_argument("--out", default="/tmp/nyrqis_gui",
                        help="Output directory (default /tmp/nyrqis_gui)")
    parser.add_argument("--no-gifs", action="store_true",
                        help="Skip animated GIF generation")
    parser.add_argument("--size", default="1280x720",
                        help="Render size, WxH (default 1280x720)")
    args = parser.parse_args()

    try:
        w, h = (int(x) for x in args.size.lower().split("x"))
    except ValueError:
        parser.error("--size must look like 1280x720")

    run_gui(args.out, gifs=not args.no_gifs, width=w, height=h)


if __name__ == "__main__":
    main()
