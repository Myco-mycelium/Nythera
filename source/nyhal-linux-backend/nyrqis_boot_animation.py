#!/usr/bin/env python3
"""
Nyrqis OS — Boot Sequence Animation

Renders a complete boot sequence as PNG frames:
1. BIOS/firmware splash
2. Kernel loading
3. Service startup (systemd-style)
4. Wayland compositor init
5. Shell loading
6. Desktop ready

Usage:
    python3 nyrqis_boot_animation.py                    # render all frames
    python3 nyrqis_boot_animation.py --frames 30        # custom frame count
    python3 nyrqis_boot_animation.py --output /tmp/boot # custom output dir
    python3 nyrqis_boot_animation.py --as-gif           # also create GIF
"""

import argparse
import math
import os
import sys
import time
from typing import Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Pillow required: pip install Pillow")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------

BG_DARK = (12, 12, 20)
BG_BOOT = (15, 15, 28)
ACCENT = (80, 180, 255)
ACCENT_DIM = (40, 100, 160)
GREEN = (80, 200, 120)
RED = (220, 80, 80)
YELLOW = (220, 180, 60)
WHITE = (220, 220, 230)
GRAY = (100, 100, 120)
DARK_GRAY = (40, 40, 55)


# ---------------------------------------------------------------------------
# Font helper
# ---------------------------------------------------------------------------

def get_font(size: int) -> ImageFont.FreeTypeFont:
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    ]
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def get_bold_font(size: int) -> ImageFont.FreeTypeFont:
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except (OSError, IOError):
            continue
    return get_font(size)


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------

def draw_progress_bar(draw, x, y, w, h, progress, color=ACCENT, bg=DARK_GRAY):
    draw.rectangle([x, y, x + w, y + h], fill=bg)
    fill_w = int(w * min(1.0, progress))
    if fill_w > 0:
        draw.rectangle([x, y, x + fill_w, y + h], fill=color)


def draw_centered_text(draw, y, text, font, fill=WHITE, width=1280):
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    draw.text(((width - tw) // 2, y), text, fill=fill, font=font)


def draw_nyrqis_logo(draw, cx, cy, size=60, color=ACCENT):
    """Draw a stylized mushroom/Nyrqis logo."""
    # Cap
    draw.ellipse([cx - size, cy - size, cx + size, cy], fill=color)
    # Stem
    draw.rectangle([cx - size // 3, cy, cx + size // 3, cy + size], fill=color)
    # Inner glow
    draw.ellipse([cx - size // 2, cy - size // 2, cx + size // 2, cy + size // 4],
                 fill=(color[0] // 2, color[1] // 2, color[2] // 2))


def draw_scanlines(img, alpha=15):
    """Add subtle CRT scanline effect."""
    draw = ImageDraw.Draw(img)
    for y in range(0, img.height, 3):
        draw.line([(0, y), (img.width, y)], fill=(0, 0, 0, alpha))


# ---------------------------------------------------------------------------
# Boot phases
# ---------------------------------------------------------------------------

def render_bios_splash(frame: int, total: int) -> Image.Image:
    """Phase 1: BIOS/firmware splash with Nyrqis logo."""
    img = Image.new("RGB", (1280, 720), BG_DARK)
    draw = ImageDraw.Draw(img)
    font = get_bold_font(28)
    sfont = get_font(16)

    progress = frame / max(total - 1, 1)

    # Logo
    draw_nyrqis_logo(draw, 640, 250, size=80, color=ACCENT)

    # Title
    draw_centered_text(draw, 360, "NYRQIS OS", get_bold_font(48), ACCENT)
    draw_centered_text(draw, 420, "v0.1.0 — Mycelium Build", get_font(18), GRAY)

    # Progress bar
    draw_progress_bar(draw, 340, 500, 600, 6, progress)

    # Status text
    status = "Initializing firmware..." if progress < 0.3 else \
             "Detecting hardware..." if progress < 0.6 else \
             "Loading Nyrqis..."
    draw_centered_text(draw, 530, status, sfont, GRAY)

    # Blinking cursor
    if frame % 4 < 2:
        draw.rectangle([630, 580, 650, 600], fill=ACCENT)

    return img


def render_kernel_load(frame: int, total: int) -> Image.Image:
    """Phase 2: Kernel loading with text output."""
    img = Image.new("RGB", (1280, 720), BG_BOOT)
    draw = ImageDraw.Draw(img)
    font = get_font(14)
    bfont = get_bold_font(16)

    progress = frame / max(total - 1, 1)

    # Header
    draw.text((20, 10), "Nyrqis Kernel 1.0.0-rc1", fill=ACCENT, font=bfont)
    draw.line([(20, 32), (1260, 32)], fill=DARK_GRAY)

    # Boot messages
    messages = [
        (0.0, "[    0.000000] Linux version 6.8.0-nyrqis (nyrqis@build)", WHITE),
        (0.05, "[    0.001234] Command line: root=/dev/nvme0n1p2 ro", GRAY),
        (0.1, "[    0.005678] BIOS-provided physical RAM map:", GRAY),
        (0.15, "[    0.010000] NX (Execute Disable) protection: active", GREEN),
        (0.2, "[    0.100000] CPU: AMD Ryzen 9 7950X @ 5.7GHz", WHITE),
        (0.25, "[    0.150000] Memory: 32768MB available", WHITE),
        (0.3, "[    0.200000] DRM: nvidia (nvidia-drm) loaded", GREEN),
        (0.35, "[    0.250000] GBM: allocated 1920x1080 ARGB8888", GRAY),
        (0.4, "[    0.300000] Wayland: display server protocol ready", GREEN),
        (0.45, "[    0.350000] Input: evdev registered (keyboard, mouse)", GRAY),
        (0.5, "[    0.400000] NVMe: Samsung 990 Pro 2TB detected", WHITE),
        (0.55, "[    0.450000] Btrfs: mounted on /home", GREEN),
        (0.6, "[    0.500000] Nyrqis: HAL backend initialized", ACCENT),
        (0.65, "[    0.550000] Nyrqis: Compositor module loaded", ACCENT),
        (0.7, "[    0.600000] Nyrqis: Shell runtime ready", ACCENT),
        (0.8, "[    0.700000] Starting nyrqis-compositor.service...", GREEN),
        (0.85, "[    0.750000] Starting nyrqis-shell.service...", GREEN),
        (0.9, "[    0.800000] Desktop session starting...", GREEN),
        (0.95, "[    0.900000] ═══ Nyrqis OS ready ═══", ACCENT),
    ]

    y = 50
    for threshold, msg, color in messages:
        if progress >= threshold:
            draw.text((20, y), msg, fill=color, font=font)
            y += 20

    # Progress bar at bottom
    draw_progress_bar(draw, 20, 690, 1240, 8, progress)

    return img


def render_service_startup(frame: int, total: int) -> Image.Image:
    """Phase 3: Systemd-style service startup."""
    img = Image.new("RGB", (1280, 720), BG_BOOT)
    draw = ImageDraw.Draw(img)
    font = get_font(14)
    bfont = get_bold_font(16)

    progress = frame / max(total - 1, 1)

    draw.text((20, 10), "Nyrqis OS — Service Manager", fill=ACCENT, font=bfont)
    draw.line([(20, 32), (1260, 32)], fill=DARK_GRAY)

    services = [
        (0.0, "nyrqis-kernel.service", "Loaded", GREEN),
        (0.05, "systemd-journald.service", "Running", GREEN),
        (0.1, "systemd-udevd.service", "Running", GREEN),
        (0.15, "NetworkManager.service", "Running", GREEN),
        (0.2, "sshd.service", "Running", GREEN),
        (0.25, "bluetooth.service", "Running", GREEN),
        (0.3, "cups.service", "Running", GREEN),
        (0.35, "nyrqis-compositor.service", "Starting...", YELLOW),
        (0.5, "nyrqis-compositor.service", "Running", GREEN),
        (0.55, "nyrqis-shell.service", "Starting...", YELLOW),
        (0.7, "nyrqis-shell.service", "Running", GREEN),
        (0.75, "nyrqis-desktop.service", "Starting...", YELLOW),
        (0.85, "nyrqis-desktop.service", "Running", GREEN),
        (0.9, "nyrqis-session.service", "Starting...", YELLOW),
        (0.95, "nyrqis-session.service", "Running", GREEN),
    ]

    y = 50
    for threshold, name, status, color in services:
        if progress >= threshold:
            icon = "●" if status == "Running" else "◌"
            status_color = GREEN if status == "Running" else YELLOW
            draw.text((20, y), f"  {icon} {name}", fill=WHITE, font=font)
            draw.text((500, y), status, fill=status_color, font=font)
            y += 22

    draw_progress_bar(draw, 20, 690, 1240, 8, progress)
    return img


def render_compositor_init(frame: int, total: int) -> Image.Image:
    """Phase 4: Wayland compositor initialization."""
    img = Image.new("RGB", (1280, 720), BG_DARK)
    draw = ImageDraw.Draw(img)
    font = get_font(14)
    bfont = get_bold_font(16)
    sfont = get_font(13)

    progress = frame / max(total - 1, 1)

    draw_nyrqis_logo(draw, 640, 120, size=50, color=ACCENT)
    draw_centered_text(draw, 190, "Nyrqis Compositor", bfont, ACCENT)

    # GPU info
    draw.text((100, 240), "GPU:", fill=GRAY, font=font)
    draw.text((160, 240), "NVIDIA RTX 4090 (nvidia-drm)", fill=WHITE, font=font)

    draw.text((100, 265), "Display:", fill=GRAY, font=font)
    draw.text((190, 265), "1920×1080@144Hz (HDMI-A-1)", fill=WHITE, font=font)

    draw.text((100, 290), "Renderer:", fill=GRAY, font=font)
    draw.text((200, 290), "Vulkan 1.3 / GBM / DRM", fill=WHITE, font=font)

    draw.line([(100, 320), (1180, 320)], fill=DARK_GRAY)

    # Init steps
    steps = [
        (0.0, "Opening DRM device /dev/dri/card0"),
        (0.1, "Setting display mode: 1920×1080@144"),
        (0.2, "Initializing Vulkan rendering context"),
        (0.3, "Creating GBM buffer surfaces"),
        (0.4, "Starting Wayland display (wayland-0)"),
        (0.5, "Loading shell design from ~/.nyrqis/shell.nstudio"),
        (0.6, "Registering global protocols"),
        (0.7, "Creating desktop surfaces"),
        (0.8, "Initializing input devices"),
        (0.9, "Compositor ready — launching shell"),
    ]

    y = 340
    for threshold, msg in steps:
        if progress >= threshold:
            draw.text((120, y), f"  ✓ {msg}", fill=GREEN, font=sfont)
            y += 22

    draw_progress_bar(draw, 100, 680, 1080, 8, progress)
    return img


def render_shell_load(frame: int, total: int) -> Image.Image:
    """Phase 5: Shell loading with app icons."""
    img = Image.new("RGB", (1280, 720), BG_DARK)
    draw = ImageDraw.Draw(img)
    font = get_font(14)
    bfont = get_bold_font(16)

    progress = frame / max(total - 1, 1)

    draw_centered_text(draw, 30, "Nyrqis Shell", bfont, ACCENT)
    draw.line([(100, 60), (1180, 60)], fill=DARK_GRAY)

    # App loading progress
    apps = [
        (0.0, "📄", "File Manager"),
        (0.1, "💻", "Terminal"),
        (0.2, "⚙️", "Settings"),
        (0.3, "🌐", "Browser"),
        (0.4, "📝", "Text Editor"),
        (0.5, "🎵", "Music Player"),
        (0.6, "🖼️", "Image Viewer"),
        (0.7, "📊", "System Monitor"),
        (0.8, "🔧", "Utilities"),
        (0.9, "🎮", "Games"),
    ]

    cols = 5
    start_x = 200
    start_y = 100
    cell_w = 160
    cell_h = 120

    for i, (threshold, icon, name) in enumerate(apps):
        col = i % cols
        row = i // cols
        x = start_x + col * cell_w
        y = start_y + row * cell_h

        if progress >= threshold:
            alpha = min(1.0, (progress - threshold) / 0.05)
            brightness = int(alpha * 255)

            # App tile
            tile_color = (35, 35, 55) if alpha < 1.0 else (40, 40, 60)
            draw.rounded_rectangle([x, y, x + 140, y + 100],
                                   radius=10, fill=tile_color,
                                   outline=ACCENT_DIM if alpha < 1.0 else None)

            # Icon
            try:
                icon_font = get_bold_font(32)
                draw.text((x + 45, y + 10), icon, fill=WHITE, font=icon_font)
            except Exception:
                draw.text((x + 50, y + 15), "?", fill=WHITE, font=get_bold_font(32))

            # Name
            draw.text((x + 10, y + 65), name, fill=GRAY, font=get_font(12))

            # Loading indicator
            if alpha < 1.0:
                draw.text((x + 120, y + 10), "...", fill=YELLOW, font=font)
            else:
                draw.text((x + 120, y + 10), "✓", fill=GREEN, font=font)

    # Status
    loaded = sum(1 for t, _, _ in apps if progress >= t)
    draw_centered_text(draw, 380, f"Loaded {loaded}/{len(apps)} applications", font, GRAY)

    draw_progress_bar(draw, 200, 420, 880, 8, progress)
    return img


def render_desktop_ready(frame: int, total: int) -> Image.Image:
    """Phase 6: Desktop ready — final splash with fade-in."""
    img = Image.new("RGB", (1280, 720), BG_DARK)
    draw = ImageDraw.Draw(img)
    font = get_font(14)
    bfont = get_bold_font(16)

    progress = frame / max(total - 1, 1)

    # Fade-in effect
    alpha = min(1.0, progress * 2)

    # Desktop background gradient
    for y in range(720):
        t = y / 720
        r = int(12 + t * 8)
        g = int(12 + t * 4)
        b = int(20 + t * 12)
        draw.line([(0, y), (1280, y)], fill=(r, g, b))

    # Logo (fading in)
    logo_alpha = min(1.0, progress * 3)
    draw_nyrqis_logo(draw, 640, 200, size=70,
                     color=(int(ACCENT[0] * logo_alpha),
                            int(ACCENT[1] * logo_alpha),
                            int(ACCENT[2] * logo_alpha)))

    # Text
    if progress > 0.3:
        draw_centered_text(draw, 310, "Nyrqis OS", get_bold_font(52), ACCENT)
        draw_centered_text(draw, 380, "Welcome", get_font(24), GRAY)

    if progress > 0.6:
        draw_centered_text(draw, 440, "Mycelium Build v0.1.0", get_font(16), GRAY)
        draw_centered_text(draw, 470, "Desktop ready — enjoy!", get_font(14), GREEN)

    # Taskbar fade-in
    if progress > 0.7:
        tb_alpha = min(1.0, (progress - 0.7) / 0.3)
        tb_y = 680
        draw.rectangle([0, tb_y, 1280, 720],
                       fill=(int(20 * tb_alpha), int(20 * tb_alpha), int(35 * tb_alpha)))
        draw.line([(0, tb_y), (1280, tb_y)],
                  fill=(int(60 * tb_alpha), int(60 * tb_alpha), int(100 * tb_alpha)))

    return img


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

PHASES = [
    ("01_bios_splash", "BIOS/Firmware Splash", render_bios_splash, 15),
    ("02_kernel_load", "Kernel Loading", render_kernel_load, 20),
    ("03_service_startup", "Service Startup", render_service_startup, 18),
    ("04_compositor_init", "Compositor Init", render_compositor_init, 15),
    ("05_shell_load", "Shell Loading", render_shell_load, 18),
    ("06_desktop_ready", "Desktop Ready", render_desktop_ready, 15),
]


def main():
    parser = argparse.ArgumentParser(description="Nyrqis OS Boot Animation")
    parser.add_argument("--output", default="/tmp/nyrqis_boot",
                        help="Output directory")
    parser.add_argument("--frames", type=int, default=None,
                        help="Total frames (default: sum of all phases)")
    parser.add_argument("--as-gif", action="store_true",
                        help="Also create animated GIF")
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    print("╔══════════════════════════════════════════╗")
    print("║    Nyrqis OS — Boot Animation             ║")
    print("╚══════════════════════════════════════════╝")
    print()

    total_frames = sum(f[3] for f in PHASES)
    frame_num = 0
    all_frames = []

    for phase_name, phase_desc, render_fn, num_frames in PHASES:
        print(f"  Phase: {phase_desc} ({num_frames} frames)")
        for i in range(num_frames):
            progress = i / max(num_frames - 1, 1)
            img = render_fn(i, num_frames)
            fname = f"{phase_name}_{i:03d}.png"
            path = os.path.join(args.output, fname)
            img.save(path)
            all_frames.append(img)
            frame_num += 1

    print()
    print(f"  Rendered: {frame_num} frames → {args.output}/")

    if args.as_gif:
        gif_path = os.path.join(args.output, "boot_animation.gif")
        all_frames[0].save(
            gif_path,
            save_all=True,
            append_images=all_frames[1:],
            duration=80,  # ms per frame
            loop=0,
        )
        gif_size = os.path.getsize(gif_path) // 1024
        print(f"  GIF: {gif_path} ({gif_size}KB)")

    print(f"  Duration: ~{frame_num * 0.08:.1f}s at 12.5fps")
    print()
    print("  Done! 🍄")


if __name__ == "__main__":
    main()
