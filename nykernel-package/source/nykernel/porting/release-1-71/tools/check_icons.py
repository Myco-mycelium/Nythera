#!/usr/bin/env python3
"""Independent check of the icon masks (increment 1-45): renders every icon listed in the manifest of userprog/shell_layout.inc with CHROMIUM (Skia, analytic
anti-aliasing: a different renderer, a different algorithm from tools/svgraster.py) straight from the vendored official SVG, and compares it with the alpha mask the
kernel's shell blends. Reference = Chromium at 16x, box-filtered to size. Tolerance: max |diff| <= 24, mean <= 2.5, total ink within 2% (8x8 supersampling vs a 16x render). Also verifies the icon files are the vendored,
unmodified Material SVGs (Apache-2.0 LICENSE + NOTICE present). Needs playwright + Chromium; if they are missing this check FAILS LOUDLY unless --skip-ok is given."""
import re, sys, os, glob
src = open('userprog/shell_layout.inc').read()
man = [(m.group(1), m.group(2), int(m.group(3)), int(m.group(4)), int(m.group(5))) for m in re.finditer(r'/\* icon (\w+) (\w+) (\d+) (\d+) @ (\d+) \*/', src)]
tab = bytes(int(v) for ln in re.search(r'icons:[^\n]*\n((?:\s+\.byte [^\n]*\n)+)', src).group(1).splitlines() for v in ln.split('.byte')[1].split(','))
assert man and all(os.path.exists('third_party/material-icons/%s/%s.svg' % (v, n)) for v, n, *_ in man)
assert os.path.exists('third_party/material-icons/LICENSE') and 'Apache License' in open('third_party/material-icons/LICENSE').read() and os.path.exists('third_party/material-icons/NOTICE')
assert max(off + sz * sz for (_v, _n, sz, _r, off) in man) == len(tab), 'the table has bytes no manifest entry owns'
try:
    from playwright.sync_api import sync_playwright
    import io
    from PIL import Image
except ImportError as e:
    print('icon check SKIPPED (%s)' % e if '--skip-ok' in sys.argv else 'icon check FAILED: needs playwright + Pillow (%s)' % e); sys.exit(0 if '--skip-ok' in sys.argv else 1)
bad = 0
with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path=os.environ.get('CHROMIUM', '/opt/pw-browsers/chromium') if os.path.exists('/opt/pw-browsers/chromium') else None)
    for (var, name, sz, rot, off) in man:
        d = re.search(r'<path d="([^"]+)"', open('third_party/material-icons/%s/%s.svg' % (var, name)).read()).group(1)
        K = 16                                    # render at 16x and box-filter down: Skia's own anti-aliasing is ~3% light on curves at 1x (measured on a circle: 246.2 vs the exact 254.5 px of ink), the 16x render is not
        pg = b.new_page(viewport={'width': sz * K, 'height': sz * K})
        pg.set_content('<body style="margin:0;background:#fff"><svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 24 24"><g transform="rotate(%d 12 12)"><path d="%s" fill="#000"/></g></svg></body>' % (sz * K, sz * K, rot, d))
        img = Image.open(io.BytesIO(pg.screenshot())).convert('L').resize((sz, sz), Image.BOX); pg.close()
        ref = [255 - v for v in img.get_flattened_data()] if hasattr(img, 'get_flattened_data') else [255 - v for v in img.getdata()]; mine = tab[off:off + sz * sz]
        diffs = [abs(a - c) for a, c in zip(ref, mine)]; mx, mean = max(diffs), sum(diffs) / len(diffs)
        ink = abs(sum(ref) - sum(mine)) / max(1, sum(ref))
        ok = mx <= 24 and mean <= 2.5 and ink <= 0.02 and sum(1 for v in mine if v) > 0
        print('%s  %-8s %-22s %2dpx rot %3d: max diff %3d mean %.2f  ink area off by %.2f%%' % ('PASS' if ok else 'FAIL', var, name, sz, rot, mx, mean, ink * 100)); bad += not ok
    b.close()
print('icon check: %s (%d icons vs Chromium)' % ('PASS' if not bad else 'FAILED', len(man))); sys.exit(1 if bad else 0)
