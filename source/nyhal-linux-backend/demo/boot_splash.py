#!/usr/bin/env python3
"""Animated boot splash screen for Nyrqis OS.

Renders a smooth animated boot sequence as PNG frames:
1. Fade in from black with Nyrqis logo
2. Progress bar with phase labels
3. Smooth transitions between phases
4. Desktop reveal

Usage:
    python3 demo/boot_splash.py --output /tmp/nyrqis-splash --frames 30

Each frame is a 1920x1080 PNG. Combine with ffmpeg for video:
    ffmpeg -framerate 30 -i frame_%04d.png -c:v libx264 splash.mp4
"""

from __future__ import annotations

import math
import os
import struct
import sys
import zlib
from typing import List, Tuple

_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_DIR)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from ui.terminal import TerminalEmulator, TerminalConfig, AnsiColor, DEFAULT_PALETTE


# ---------------------------------------------------------------------------
# PNG writer
# ---------------------------------------------------------------------------

def _write_png(path: str, width: int, height: int, rgb_data: bytes) -> None:
    """Write an RGB image as PNG."""
    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
    
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b""
    stride = width * 3
    for y in range(height):
        raw += b"\x00"
        raw += rgb_data[y * stride : (y + 1) * stride]
    
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_chunk(b"IHDR", ihdr))
        f.write(_chunk(b"IDAT", zlib.compress(raw, 6)))
        f.write(_chunk(b"IEND", b""))


# ---------------------------------------------------------------------------
# Color utilities
# ---------------------------------------------------------------------------

def _lerp_color(a: Tuple[int, int, int], b: Tuple[int, int, int], t: float) -> Tuple[int, int, int]:
    """Linearly interpolate between two RGB colors."""
    t = max(0.0, min(1.0, t))
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


def _ease_in_out(t: float) -> float:
    """Smooth ease-in-out curve."""
    return t * t * (3 - 2 * t)


def _ease_out(t: float) -> float:
    """Ease-out curve."""
    return 1 - (1 - t) * (1 - t)


# ---------------------------------------------------------------------------
# Boot splash renderer
# ---------------------------------------------------------------------------

class BootSplashRenderer:
    """Renders animated boot splash frames.
    
    Parameters
    ----------
    width : int
        Frame width.
    height : int
        Frame height.
    fps : int
        Frames per second for timing calculations.
    """
    
    # Boot phases with timing (as fraction of total animation)
    PHASES = [
        (0.00, 0.15, "Nyrqis OS", "Kernel booting..."),
        (0.15, 0.30, "Nyrqis OS", "Loading drivers..."),
        (0.30, 0.45, "Nyrqis OS", "Initializing GPU..."),
        (0.45, 0.60, "Nyrqis OS", "Starting compositor..."),
        (0.60, 0.75, "Nyrqis OS", "Loading shell..."),
        (0.75, 0.90, "Nyrqis OS", "Preparing desktop..."),
        (0.90, 1.00, "Nyrqis OS", "Ready!"),
    ]
    
    # Colors
    BG_COLOR = (10, 10, 16)
    LOGO_COLOR = (80, 140, 255)
    TEXT_COLOR = (200, 200, 220)
    DIM_COLOR = (100, 100, 120)
    PROGRESS_BG = (40, 40, 55)
    PROGRESS_FG = (80, 140, 255)
    
    def __init__(self, width: int = 1920, height: int = 1080, fps: int = 30):
        self.width = width
        self.height = height
        self.fps = fps
    
    def render_frames(self, output_dir: str, total_frames: int = 60) -> List[str]:
        """Render all animation frames.
        
        Returns list of output file paths.
        """
        os.makedirs(output_dir, exist_ok=True)
        frames = []
        
        for frame_idx in range(total_frames):
            t = frame_idx / max(1, total_frames - 1)  # 0.0 to 1.0
            
            path = os.path.join(output_dir, f"frame_{frame_idx:04d}.png")
            self._render_frame(path, t)
            frames.append(path)
            
            if frame_idx % 10 == 0:
                print(f"  Frame {frame_idx + 1}/{total_frames} ({t:.0%})")
        
        return frames
    
    def _render_frame(self, output_path: str, t: float) -> None:
        """Render a single frame at time t (0.0 to 1.0)."""
        w = self.width
        h = self.height
        
        # Initialize pixels with background
        pixels = [self.BG_COLOR] * (w * h)
        
        def set_pixel(px: int, py: int, color: Tuple[int, int, int]) -> None:
            if 0 <= px < w and 0 <= py < h:
                pixels[py * w + px] = color
        
        def fill_rect(rx: int, ry: int, rw: int, rh: int, color: Tuple[int, int, int]) -> None:
            for dy in range(rh):
                for dx in range(rw):
                    set_pixel(rx + dx, ry + dy, color)
        
        def draw_char(cx: int, cy: int, ch: str, color: Tuple[int, int, int], scale: int = 1) -> None:
            FONT = {
                ' ': [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
                'N': [0x11, 0x11, 0x19, 0x15, 0x13, 0x11, 0x11],
                'y': [0x00, 0x00, 0x11, 0x11, 0x0F, 0x01, 0x0E],
                'r': [0x00, 0x00, 0x16, 0x19, 0x10, 0x10, 0x10],
                'q': [0x00, 0x00, 0x0D, 0x13, 0x0F, 0x01, 0x01],
                'i': [0x04, 0x00, 0x0C, 0x04, 0x04, 0x04, 0x0E],
                's': [0x00, 0x00, 0x0E, 0x10, 0x0E, 0x01, 0x1E],
                'O': [0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E],
                '.': [0x00, 0x00, 0x00, 0x00, 0x00, 0x0C, 0x0C],
                'K': [0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11],
                'e': [0x00, 0x00, 0x0E, 0x11, 0x1F, 0x10, 0x0E],
                'n': [0x00, 0x00, 0x16, 0x19, 0x11, 0x11, 0x11],
                'l': [0x0C, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E],
                'a': [0x00, 0x00, 0x0E, 0x01, 0x0F, 0x11, 0x0F],
                'd': [0x01, 0x01, 0x0D, 0x13, 0x11, 0x11, 0x0F],
                'o': [0x00, 0x00, 0x0E, 0x11, 0x11, 0x11, 0x0E],
                'g': [0x00, 0x0F, 0x11, 0x11, 0x0F, 0x01, 0x0E],
                'b': [0x10, 0x10, 0x16, 0x19, 0x11, 0x11, 0x1E],
                'c': [0x00, 0x00, 0x0E, 0x10, 0x10, 0x11, 0x0E],
                'v': [0x00, 0x00, 0x11, 0x11, 0x11, 0x0A, 0x04],
                'm': [0x00, 0x00, 0x1A, 0x15, 0x15, 0x11, 0x11],
                'p': [0x00, 0x00, 0x1E, 0x11, 0x1E, 0x10, 0x10],
                't': [0x10, 0x10, 0x1C, 0x10, 0x10, 0x10, 0x0E],
                'S': [0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E],
                'h': [0x10, 0x10, 0x16, 0x19, 0x11, 0x11, 0x11],
                'f': [0x06, 0x09, 0x08, 0x1C, 0x08, 0x08, 0x08],
                'w': [0x00, 0x00, 0x11, 0x11, 0x15, 0x15, 0x0A],
                'I': [0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E],
                'D': [0x1E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x1E],
                'R': [0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11],
                'E': [0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F],
                'A': [0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11],
                'Y': [0x11, 0x11, 0x0A, 0x04, 0x04, 0x04, 0x04],
                '!': [0x04, 0x04, 0x04, 0x04, 0x04, 0x00, 0x04],
                '0': [0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E],
                '2': [0x0E, 0x11, 0x01, 0x06, 0x08, 0x10, 0x1F],
                '5': [0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E],
                '.': [0x00, 0x00, 0x00, 0x00, 0x00, 0x0C, 0x0C],
            }
            glyph = FONT.get(ch, FONT[' '])
            for row in range(7):
                bits = glyph[row]
                for col in range(5):
                    if bits & (1 << (4 - col)):
                        for sy in range(scale):
                            for sx in range(scale):
                                set_pixel(cx + col * scale + sx, cy + row * scale + sy, color)
        
        def draw_text(tx: int, ty: int, text: str, color: Tuple[int, int, int], scale: int = 1) -> int:
            cx = tx
            for ch in text:
                draw_char(cx, ty, ch, color, scale)
                cx += 6 * scale
            return cx
        
        # === Phase calculations ===
        # Fade in from black
        fade_in = _ease_in_out(min(1.0, t * 3))  # 0-33% fade in
        
        # Current boot phase
        phase_text = "Booting..."
        phase_subtext = ""
        for start, end, main, sub in self.PHASES:
            if start <= t < end:
                phase_text = main
                phase_subtext = sub
                break
        if t >= 0.95:
            phase_text = "Nyrqis OS"
            phase_subtext = "Ready!"
        
        # === Background ===
        # Simple vertical gradient (fast)
        for y in range(h):
            t_v = y / h
            bg_r = int(10 + t_v * 6)
            bg_g = int(10 + t_v * 6)
            bg_b = int(16 + t_v * 10)
            bg = _lerp_color((0, 0, 0), (bg_r, bg_g, bg_b), fade_in)
            for x in range(w):
                pixels[y * w + x] = bg
        
        # === Logo ===
        logo_scale = 4
        logo_text = "Nyrqis"
        logo_w = len(logo_text) * 6 * logo_scale
        logo_x = (w - logo_w) // 2
        logo_y = h // 2 - 80
        
        # Logo fade
        logo_alpha = _ease_out(min(1.0, max(0, t * 4 - 0.5)))
        logo_color = _lerp_color(self.BG_COLOR, self.LOGO_COLOR, logo_alpha)
        
        draw_text(logo_x, logo_y, logo_text, logo_color, logo_scale)
        
        # === Version text ===
        ver_alpha = _ease_out(min(1.0, max(0, t * 3 - 0.8)))
        ver_color = _lerp_color(self.BG_COLOR, self.DIM_COLOR, ver_alpha)
        ver_text = "v0.25.0"
        ver_w = len(ver_text) * 6 * 2
        draw_text((w - ver_w) // 2, logo_y + 40, ver_text, ver_color, 2)
        
        # === Progress bar ===
        bar_w = 400
        bar_h = 8
        bar_x = (w - bar_w) // 2
        bar_y = h // 2 + 40
        
        bar_bg_alpha = _ease_out(min(1.0, max(0, t * 3 - 1.0)))
        bar_bg = _lerp_color(self.BG_COLOR, self.PROGRESS_BG, bar_bg_alpha)
        bar_fg = _lerp_color(self.BG_COLOR, self.PROGRESS_FG, bar_bg_alpha)
        
        # Bar background
        fill_rect(bar_x, bar_y, bar_w, bar_h, bar_bg)
        
        # Bar fill (smooth progress)
        fill_w = int(bar_w * _ease_in_out(t))
        fill_rect(bar_x, bar_y, fill_w, bar_h, bar_fg)
        
        # Bar glow effect
        glow_color = _lerp_color(self.BG_COLOR, (100, 160, 255), bar_bg_alpha * 0.3)
        fill_rect(bar_x, bar_y - 1, fill_w, 1, glow_color)
        fill_rect(bar_x, bar_y + bar_h, fill_w, 1, glow_color)
        
        # === Phase text ===
        text_alpha = _ease_out(min(1.0, max(0, t * 3 - 1.2)))
        text_color = _lerp_color(self.BG_COLOR, self.TEXT_COLOR, text_alpha)
        dim_color = _lerp_color(self.BG_COLOR, self.DIM_COLOR, text_alpha)
        
        phase_w = len(phase_subtext) * 6 * 2
        draw_text((w - phase_w) // 2, bar_y + 30, phase_subtext, dim_color, 2)
        
        # === Percentage ===
        pct = f"{int(t * 100)}%"
        pct_w = len(pct) * 6 * 2
        draw_text((w - pct_w) // 2, bar_y + 16, pct, text_color, 2)
        
        # === Decorative elements ===
        # Subtle horizontal lines
        line_alpha = _ease_out(min(1.0, max(0, t * 2 - 0.5)))
        line_color = _lerp_color(self.BG_COLOR, (30, 30, 40), line_alpha)
        for i in range(3):
            ly = logo_y - 30 + i * (80 + 30)
            fill_rect(w // 4, ly, w // 2, 1, line_color)
        
        # === Desktop reveal (last 10%) ===
        if t > 0.9:
            reveal_t = (t - 0.9) / 0.1  # 0 to 1 in last 10%
            reveal_alpha = _ease_in_out(reveal_t)
            
            # Desktop gradient - only blend the taskbar area
            taskbar_h = int(48 * reveal_alpha)
            if taskbar_h > 0:
                taskbar_y = h - taskbar_h
                for y in range(taskbar_y, h):
                    for x in range(w):
                        old = pixels[y * w + x]
                        blended = _lerp_color(old, (32, 32, 44), reveal_alpha * 0.5)
                        pixels[y * w + x] = blended
            
            # Taskbar
            taskbar_h = int(48 * reveal_alpha)
            if taskbar_h > 0:
                taskbar_y = h - taskbar_h
                fill_rect(0, taskbar_y, w, taskbar_h, (32, 32, 44))
                fill_rect(0, taskbar_y, w, 1, (60, 60, 80))
                
                # Start button
                if taskbar_h > 20:
                    fill_rect(8, taskbar_y + 8, 32, min(32, taskbar_h - 16), (80, 140, 255))
        
        # === Render to RGB ===
        rgb_data = bytearray(w * h * 3)
        i = 0
        for r, g, b in pixels:
            rgb_data[i] = r
            rgb_data[i+1] = g
            rgb_data[i+2] = b
            i += 3
        
        _write_png(output_path, w, h, bytes(rgb_data))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    """Render the boot splash animation."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Nyrqis Boot Splash Animation")
    parser.add_argument("--output", "-o", default="/tmp/nyrqis-splash",
                       help="Output directory for frames")
    parser.add_argument("--frames", "-n", type=int, default=60,
                       help="Number of frames to render")
    parser.add_argument("--width", type=int, default=1920,
                       help="Frame width")
    parser.add_argument("--height", type=int, default=1080,
                       help="Frame height")
    parser.add_argument("--fps", type=int, default=30,
                       help="Frames per second")
    args = parser.parse_args()
    
    print("╔══════════════════════════════════════════╗")
    print("║      Nyrqis Boot Splash Animation        ║")
    print("╚══════════════════════════════════════════╝")
    print()
    print(f"Resolution: {args.width}x{args.height}")
    print(f"Frames: {args.frames} @ {args.fps}fps")
    print(f"Duration: {args.frames / args.fps:.1f}s")
    print(f"Output: {args.output}")
    print()
    
    renderer = BootSplashRenderer(args.width, args.height, args.fps)
    frames = renderer.render_frames(args.output, args.frames)
    
    print()
    print("════════════════════════════════════════════")
    print(f"  Rendered {len(frames)} frames!")
    print(f"  Output: {args.output}")
    print()
    print("  To create video:")
    print(f"    ffmpeg -framerate {args.fps} -i {args.output}/frame_%04d.png \\")
    print(f"           -c:v libx264 -pix_fmt yuv420p splash.mp4")
    print("════════════════════════════════════════════")


if __name__ == "__main__":
    main()
