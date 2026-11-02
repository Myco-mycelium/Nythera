"""Build-time glyph rasterizer (increment 1-46): Roboto (SIL OFL-1.1, vendored unmodified in third_party/roboto) -> per-face alpha-mask tables for ASCII 32..126, via FreeType (Pillow).
FACES are (weight, pixel size) pairs = Material type roles: label-medium 12/500, body-medium 14/400, body-large 16/400, title-large 22/400 (1 dp = 1 px here).
Layout model: the pen starts at x, the BASELINE is the record's y, glyph g is drawn at (x + bx, y + top) with `top` negative above the baseline, the pen then advances by the
rounded advance width. No kerning (documented limit). The independent check is tools/check_fonts.py (Chromium)."""
import os
from PIL import Image, ImageDraw, ImageFont
from fontTools.ttLib import TTFont            # advance widths come from the font's own hmtx table (unhinted), because FreeType's hinted advances at 12-22 px are up to 2 px off the design widths
HERE = os.path.dirname(os.path.abspath(__file__)); DIR = os.path.join(HERE, '..', 'third_party', 'roboto')
ROLES = [500, 400, 400, 400]                       # weights of label-medium, body-medium, body-large, title-large
SCALE_SIZES = [(12, 14, 16, 22), (14, 17, 19, 26), (18, 22, 25, 32)]       # font scale 100% / 130% / 200% (1-47): a NONLINEAR curve in the spirit of Android 14's font scaling -- small text grows
                                                                           # proportionally more than large text, so the 192-dp band still fits (this is NOT Android's lookup table)
FACES = [(ROLES[r], SCALE_SIZES[sc][r]) for sc in range(3) for r in range(4)]         # face id = 4 * scale + role
FIRST, COUNT = 32, 95
S = 8
def build(faces=None):
    masks = bytearray(); table = []; heads = []
    for weight, px in (FACES if faces is None else faces):
        f = ImageFont.truetype(os.path.join(DIR, 'roboto-latin-%d-normal.woff' % weight), px * S, layout_engine=ImageFont.Layout.BASIC)       # drawn at 8x and box-filtered down: FreeType's 1x hinting thickens thin strokes
        asc, desc = (-(-v // S) for v in f.getmetrics()); heads.append((asc, desc)); gl = []
        tt = TTFont(os.path.join(DIR, 'roboto-latin-%d-normal.woff' % weight)); cmap = tt.getBestCmap(); upm = tt['head'].unitsPerEm; hm = tt['hmtx']
        for code in range(FIRST, FIRST + COUNT):
            ch = chr(code); adv = int(round(hm[cmap[code]][0] * px / upm)); L, T, R, B = f.getbbox(ch, anchor='ls')
            l, t, r, b = L // S, T // S, -(-R // S), -(-B // S)
            w, h = max(0, r - l), max(0, b - t)
            if w == 0 or h == 0 or ch == ' ': gl.append((0, 0, 0, 0, 0, adv)); continue
            big = Image.new('L', (w * S, h * S), 0); ImageDraw.Draw(big).text((-l * S, -t * S), ch, font=f, fill=255, anchor='ls'); im = big.resize((w, h), Image.BOX)
            assert -128 <= l < 128 and -128 <= t < 128 and w < 256 and h < 256
            gl.append((len(masks), w, h, l, t, adv)); masks.extend(im.tobytes())
        table.append(gl)
    return bytes(masks), table, heads
def width(table, face, s): return sum(table[face][ord(c) - FIRST][5] for c in s)
