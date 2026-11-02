#!/usr/bin/env python3
"""Independent check of the Roboto glyph tables (increment 1-46). For every face (weight, pixel size) and every ASCII glyph 32..126 it renders the glyph with CHROMIUM (canvas
fillText from the same vendored woff, at 16x and box-filtered down: a different rasterizer from FreeType/Pillow), places it at the pen origin the shell uses (baseline y, pen x,
bitmap at (bx, top)) and compares it with the alpha mask in userprog/shell_layout.inc. Also compares the advance widths with Chromium's measureText. FreeType hints at 1x and
Chromium does not, so stems may differ by a fraction of a pixel: tolerance = mean |diff| <= 4/255 per glyph cell and ink area within 10% (or 1.5 px^2 for tiny glyphs such as the dot); advance within 0.51 px (the table rounds the unhinted design advance). Needs playwright."""
import re, sys, os, base64
src = open('userprog/shell_layout.inc').read()
hd = [tuple(int(v) for v in ln.split('.long')[1].split(',')) for ln in re.search(r'font_hdr:\n((?:\s+\.long [^\n]*\n){12})', src).group(1).splitlines()]
rows = [tuple(int(v, 0) for v in ln.split('.long')[1].split(',')) for ln in re.search(r'font_gl:[^\n]*\n((?:\s+\.long [^\n]*\n){1140})', src).group(1).splitlines()]
mask = bytes(int(v) for ln in re.search(r'font_masks:[^\n]*\n((?:\s+\.byte [^\n]*\n)+)', src).group(1).splitlines() for v in ln.split('.byte')[1].split(','))
FACES = [(500, 12), (400, 14), (400, 16), (400, 22), (500, 14), (400, 17), (400, 19), (400, 26), (500, 18), (400, 22), (400, 25), (400, 32)]   # written out here, not imported: 3 scales x 4 roles
assert os.path.exists('third_party/roboto/LICENSE') and 'SIL OPEN FONT LICENSE' in open('third_party/roboto/LICENSE').read().upper() and os.path.exists('third_party/roboto/NOTICE')
try:
    from playwright.sync_api import sync_playwright
    import io
    from PIL import Image
except ImportError as e:
    print('font check SKIPPED (%s)' % e if '--skip-ok' in sys.argv else 'font check FAILED: needs playwright + Pillow (%s)' % e); sys.exit(0 if '--skip-ok' in sys.argv else 1)
K, bad, worst = 16, 0, [0, 0, 0]
with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path='/opt/pw-browsers/chromium' if os.path.exists('/opt/pw-browsers/chromium') else None)
    for face, (wt, px) in enumerate(FACES):
        woff = base64.b64encode(open('third_party/roboto/roboto-latin-%d-normal.woff' % wt, 'rb').read()).decode()
        cell = 2 * px + 8; base = px + 4; pad = 4
        pg = b.new_page(viewport={'width': cell * K, 'height': cell * K})
        pg.set_content('<style>@font-face{font-family:R;src:url(data:font/woff;base64,%s)} body{margin:0;background:#fff}</style><canvas id=c width=%d height=%d></canvas>' % (woff, cell * K, cell * K))
        pg.evaluate("document.fonts.load('%dpx R')" % (px * K)); pg.evaluate("document.fonts.ready")
        for idx in range(95):
            ch = chr(32 + idx); off, pk, adv = rows[face * 95 + idx]; w, h = pk & 255, pk >> 8 & 255; bx = pk >> 16 & 255; top = pk >> 24 & 255; bx -= 256 * (bx > 127); top -= 256 * (top > 127)
            r = pg.evaluate("""([ch, f, W, bs, pd, wt]) => { const c = document.getElementById('c'), x = c.getContext('2d'); x.fillStyle = '#fff'; x.fillRect(0, 0, W, W); x.fillStyle = '#000'; x.font = wt + ' ' + f + 'px R'; x.textBaseline = 'alphabetic'; x.fillText(ch, pd, bs); return x.measureText(ch).width / %d; }""" % K, [ch, px * K, cell * K, base * K, pad * K, wt])
            img = Image.new('L', (cell * K, cell * K), 255); png = pg.locator('#c').screenshot(); img = Image.open(io.BytesIO(png)).convert('L').resize((cell, cell), Image.BOX)
            ref = [255 - v for v in (img.get_flattened_data() if hasattr(img, 'get_flattened_data') else img.getdata())]
            mine = [0] * (cell * cell)
            for dy in range(h):
                for dx in range(w):
                    X, Y = pad + bx + dx, base + top + dy
                    assert 0 <= X < cell and 0 <= Y < cell, ('glyph outside the check cell', face, ch)
                    mine[Y * cell + X] = mask[off + dy * w + dx]
            mean = sum(abs(a - c) for a, c in zip(ref, mine)) / (cell * cell); ink = abs(sum(ref) - sum(mine)) / max(1, sum(ref)) if sum(ref) else 0.0
            ink_abs = abs(sum(ref) - sum(mine)) / 255.0; ok = mean <= 4 and (ink <= 0.10 or ink_abs <= 1.5) and abs(r - adv) <= 0.51 and (ch == ' ' or sum(mine) > 0)
            worst = [max(worst[0], mean), max(worst[1], ink), max(worst[2], abs(r - adv))]
            if not ok: bad += 1; print('FAIL face %d (%d/%dpx) %r: mean %.1f ink %.1f%% adv mine %d chromium %.2f' % (face, wt, px, ch, mean, ink * 100, adv, r))
        pg.close()
    b.close()
print('font check: %s (12 faces x 95 glyphs vs Chromium; worst mean %.1f/255, worst ink area off %.1f%%, worst advance off %.2f px)' % ('PASS' if not bad else 'FAILED (%d glyphs)' % bad, *[worst[0], worst[1] * 100, worst[2]])); sys.exit(1 if bad else 0)
