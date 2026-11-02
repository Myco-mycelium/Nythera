"""Tiny build-time SVG path rasterizer for the Material icons (single <path>, 24x24 viewBox, nonzero fill). Used ONLY by make_shell_layout.py; the independent
check (tools/check_icons.py) renders the same SVGs with Chromium and compares. Output: size*size alpha bytes (0..255), 8x8 supersampling."""
import re, math
def _tokens(d):
    for m in re.finditer(r'([MmLlHhVvCcSsQqTtAaZz])|(-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)', d):
        yield m.group(1) if m.group(1) else float(m.group(2))
def _arc(x0, y0, rx, ry, phi, fa, fs, x, y, out):
    if rx == 0 or ry == 0: out.append((x, y)); return
    rx, ry = abs(rx), abs(ry); ph = math.radians(phi); c, s = math.cos(ph), math.sin(ph)
    dx, dy = (x0 - x) / 2, (y0 - y) / 2; x1, y1 = c * dx + s * dy, -s * dx + c * dy
    lam = x1 * x1 / (rx * rx) + y1 * y1 / (ry * ry)
    if lam > 1: k = math.sqrt(lam); rx *= k; ry *= k
    num = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1; den = rx * rx * y1 * y1 + ry * ry * x1 * x1
    co = math.sqrt(max(0, num / den)) * (-1 if fa == fs else 1)
    cx1, cy1 = co * rx * y1 / ry, -co * ry * x1 / rx
    cx, cy = c * cx1 - s * cy1 + (x0 + x) / 2, s * cx1 + c * cy1 + (y0 + y) / 2
    a = lambda ux, uy, vx, vy: math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
    t1 = a(1, 0, (x1 - cx1) / rx, (y1 - cy1) / ry); dt = a((x1 - cx1) / rx, (y1 - cy1) / ry, (-x1 - cx1) / rx, (-y1 - cy1) / ry)
    if not fs and dt > 0: dt -= 2 * math.pi
    elif fs and dt < 0: dt += 2 * math.pi
    n = max(8, int(abs(dt) / (math.pi / 32)) + 1)
    for i in range(1, n + 1):
        t = t1 + dt * i / n; ex, ey = rx * math.cos(t), ry * math.sin(t)
        out.append((c * ex - s * ey + cx, s * ex + c * ey + cy))
def polygons(d):
    """-> list of closed polylines (each a list of (x, y))."""
    tk = list(_tokens(d)); i = 0; polys = []; cur = []; x = y = sx = sy = 0.0; cmd = None; lc = None
    def num():
        nonlocal i; v = tk[i]; i += 1; return v
    def flag():                                   # arc flags may be glued to the next number ("0 01 10"): tokens already split numbers greedily, which is fine for these icons
        return int(num())
    while i < len(tk):
        if isinstance(tk[i], str): cmd = tk[i]; i += 1
        rel = cmd.islower(); C = cmd.upper()
        if C == 'Z':
            if cur: polys.append(cur); cur = []
            x, y = sx, sy; lc = None; continue
        if C == 'M':
            if cur: polys.append(cur)
            nx, ny = num(), num(); x, y = (x + nx, y + ny) if rel else (nx, ny); sx, sy = x, y; cur = [(x, y)]; cmd = 'l' if rel else 'L'; lc = None
        elif C == 'L': nx, ny = num(), num(); x, y = (x + nx, y + ny) if rel else (nx, ny); cur.append((x, y)); lc = None
        elif C == 'H': nx = num(); x = x + nx if rel else nx; cur.append((x, y)); lc = None
        elif C == 'V': ny = num(); y = y + ny if rel else ny; cur.append((x, y)); lc = None
        elif C in 'CS':
            if C == 'C':
                x1, y1 = num(), num()
                if rel: x1 += x; y1 += y
            else: x1, y1 = (2 * x - lc[0], 2 * y - lc[1]) if lc else (x, y)     # S: reflect the previous cubic's second control point
            x2, y2, ex, ey = num(), num(), num(), num()
            if rel: x2 += x; y2 += y; ex += x; ey += y
            for k in range(1, 17):
                t = k / 16; u = 1 - t
                cur.append((u**3 * x + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t**3 * ex, u**3 * y + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t**3 * ey))
            lc = (x2, y2); x, y = ex, ey
        elif C == 'A':
            rx, ry, phi = num(), num(), num(); fa, fs = flag(), flag(); ex, ey = num(), num()
            if rel: ex += x; ey += y
            _arc(x, y, rx, ry, phi, fa, fs, ex, ey, cur); x, y = ex, ey; lc = None
        else: raise ValueError('unsupported path command ' + cmd)
    if cur: polys.append(cur)
    return polys
def fill(polys, w, h, ss=8):
    """Nonzero-winding coverage of closed polylines given in PIXEL coordinates (origin top-left of a w x h bitmap) -> w*h alpha bytes, ss x ss supersampling."""
    edges = []
    for pts in polys:
        for a, b in zip(pts, pts[1:] + pts[:1]):
            if a[1] != b[1]: edges.append((a, b))
    cov = [[0] * w for _ in range(h)]
    for sy in range(h * ss):
        yy = (sy + 0.5) / ss; xs = []
        for (ax, ay), (bx, by) in edges:
            if (ay <= yy < by) or (by <= yy < ay):
                xs.append((ax + (yy - ay) * (bx - ax) / (by - ay), 1 if by > ay else -1))
        xs.sort(); wn = 0
        for k in range(len(xs) - 1):
            wn += xs[k][1]
            if wn != 0:                           # nonzero winding: the span [xs[k], xs[k+1]) is inside
                lo = max(0, math.ceil(xs[k][0] * ss - 0.5)); hi = min(w * ss, math.ceil(xs[k + 1][0] * ss - 0.5))
                for sx in range(lo, hi): cov[sy // ss][sx // ss] += 1
    return bytes(min(255, (c * 255 + ss * ss // 2) // (ss * ss)) for row in cov for c in row)
def raster(d, size, rot=0, ss=8):
    def tf(p):                                    # viewBox 0..24 -> 0..size, optional 90-degree turns about the centre
        x, y = p
        for _ in range(rot // 90 % 4): x, y = 24 - y, x
        return x * size / 24, y * size / 24
    return fill([[tf(p) for p in pl] for pl in polygons(d)], size, size, ss)
def load(path):
    s = open(path).read(); m = re.findall(r'<path[^>]*? d="([^"]+)"', s); assert len(m) == 1 and 'viewBox="0 0 24 24"' in s, path; return m[0]
