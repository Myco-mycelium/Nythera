#!/usr/bin/env python3
"""Rasterise the shell's real typefaces into the kernel's glyph atlas.

The shell in `porting/release-1-71/shell/src/ui.rs` is a pixel port of the
delivered HTML mock, and the mock sets its type in Bricolage Grotesque (display)
and IBM Plex Sans / IBM Plex Mono (body, mono). The kernel has no font engine,
so this tool does the rasterising on the build host and emits one binary the
renderer parses at run time:

    magic  "NYFA" | u32 version | u32 face_count
    per face:  u16 name_len | name bytes | u16 px | i16 ascent | i16 descent
               i16 line_height | u16 glyph_count
    per glyph: u8 present | i16 advance_16ths | u8 w | u8 h | i8 ox | i8 oy
               u32 data_len | 8-bit alpha bitmap (w*h)

`present == 0` means the character is absent and only its advance is valid.
Metrics are in CSS pixels: the mock's own units, which the renderer treats as
framebuffer pixels, so text lands at the sizes the stylesheet asks for.

Usage: tools/gen_font_atlas.py <fonts-dir> <out.bin>
The fonts are the Google Fonts releases of the three families (variable
Bricolage Grotesque and IBM Plex Sans, static IBM Plex Mono).
"""
import struct
import sys

from PIL import Image, ImageDraw, ImageFont

# (family file, weight axis, [sizes in CSS px]) -- exactly the sizes the mock's
# stylesheet uses for the chrome, the launcher, the control centre and the apps.
FACES = [
    ("BricolageGrotesque[opsz,wdth,wght].ttf", 700, "display700", [12, 13, 14, 15, 22, 26, 40, 64]),
    ("IBMPlexSans[wdth,wght].ttf", 400, "body400", [14, 15]),
    ("IBMPlexSans[wdth,wght].ttf", 500, "body500", [12, 13]),
    ("IBMPlexSans[wdth,wght].ttf", 600, "body600", [13]),
    ("IBMPlexMono-Regular.ttf", 400, "mono400", [11, 12, 13]),
    ("IBMPlexMono-Medium.ttf", 500, "mono500", [11, 12]),
]

CHARS = [chr(c) for c in range(32, 127)]


def load(path, size, weight):
    f = ImageFont.truetype(path, size)
    try:
        axes = f.get_variation_axes()
        values = [a["default"] for a in axes]
        for i, a in enumerate(axes):
            if a.get("name") in (b"Weight", "Weight") or "eight" in str(a.get("name")):
                values[i] = weight
        f.set_variation_by_axes(values)
    except Exception:
        pass
    return f


def main():
    fonts_dir, out = sys.argv[1], sys.argv[2]
    blob = bytearray()
    faces = []
    for path, weight, name, sizes in FACES:
        for px in sizes:
            font = load(f"{fonts_dir}/{path}", px, weight)
            ascent, descent = font.getmetrics()
            glyphs = []
            for ch in CHARS:
                adv = int(round(font.getlength(ch) * 16))
                bbox = font.getbbox(ch, anchor="ls")
                if bbox is None:
                    glyphs.append((0, adv, 0, 0, 0, 0, b""))
                    continue
                x0, y0, x1, y1 = bbox
                w, h = max(0, x1 - x0), max(0, y1 - y0)
                if w <= 0 or h <= 0:
                    glyphs.append((0, adv, 0, 0, 0, 0, b""))
                    continue
                img = Image.new("L", (w, h), 0)
                d = ImageDraw.Draw(img)
                d.text((-x0, -y0), ch, font=font, fill=255, anchor="ls")
                glyphs.append((1, adv, w, h, x0, y0, img.tobytes()))
            faces.append((name, px, ascent, descent, glyphs))

    blob += b"NYFA" + struct.pack("<II", 1, len(faces))
    for name, px, ascent, descent, glyphs in faces:
        nm = name.encode()
        blob += struct.pack("<H", len(nm)) + nm
        blob += struct.pack("<HhhhH", px, ascent, -descent, ascent + descent, len(glyphs))
        for present, adv, w, h, ox, oy, data in glyphs:
            blob += struct.pack("<BhBBbbI", present, adv, w, h, ox, oy, len(data)) + data
    with open(out, "wb") as fh:
        fh.write(blob)
    print(f"{out}: {len(blob)} bytes, {len(faces)} faces")


if __name__ == "__main__":
    main()
