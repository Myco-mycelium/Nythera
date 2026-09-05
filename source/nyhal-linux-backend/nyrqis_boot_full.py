#!/usr/bin/env python3
"""
Nyrqis OS — Full Boot Sequence Animation

Renders a complete boot-to-desktop sequence as PNG frames + animated GIF:

  1. BIOS/firmware splash (Nyrqis logo)
  2. Kernel loading (with progress bar)
  3. Backend detection (Linux DRM vs Nyrqis kernel vs Headless)
  4. Service startup (systemd-style)
  5. Compositor init (surface creation)
  6. Shell loading (taskbar, clock)
  7. Desktop ready (windows, wallpaper, UI)

Integrates with the backend abstraction layer to show the actual
boot path for each backend type.

Usage:
    python3 nyrqis_boot_full.py                     # render GIF
    python3 nyrqis_boot_full.py --backend nyrqis    # Nyrqis kernel path
    python3 nyrqis_boot_full.py --backend linux     # Linux DRM path
    python3 nyrqis_boot_full.py --output /tmp/boot  # custom output dir
    python3 nyrqis_boot_full.py --frames 150        # custom frame count
"""

import argparse
import math
import os
import sys
import time

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
BG_BLACK = (8, 8, 14)
BG_BOOT = (12, 12, 24)
ACCENT = (80, 180, 255)
ACCENT_DIM = (30, 70, 120)
ACCENT_GLOW = (40, 100, 180)
GREEN = (60, 200, 100)
GREEN_DIM = (20, 80, 40)
RED = (220, 70, 70)
YELLOW = (220, 180, 60)
WHITE = (220, 220, 230)
GRAY = (80, 80, 100)
DARK_GRAY = (40, 40, 55)
WALLPAPER_1 = (25, 25, 45)
WALLPAPER_2 = (15, 15, 35)
DESKTOP_BG = (20, 20, 38)
TASKBAR_BG = (18, 18, 32)
WINDOW_BG = (28, 28, 48)
WINDOW_TITLE = (35, 35, 55)
TEXT_DIM = (120, 120, 140)

W, H = 1280, 720


def get_font(size: int):
    """Get a monospace font, falling back to default."""
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
        "/usr/share/fonts/liberation-mono/LiberationMono-Regular.ttf",
    ]
    for p in paths:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def get_bold_font(size: int):
    """Get a bold monospace font."""
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSansMono-Bold.ttf",
    ]
    for p in paths:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return get_font(size)


def get_sans_font(size: int):
    """Get a sans-serif font."""
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for p in paths:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return get_font(size)


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------

def draw_gradient_bg(img: Image.Image, top_color, bot_color):
    """Draw a vertical gradient background."""
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        r = int(top_color[0] * (1 - t) + bot_color[0] * t)
        g = int(top_color[1] * (1 - t) + bot_color[1] * t)
        b = int(top_color[2] * (1 - t) + bot_color[2] * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b))


def draw_centered_text(draw: ImageDraw.Draw, text: str, y: int, font, fill=WHITE):
    """Draw centered text."""
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    draw.text(((W - tw) // 2, y), text, fill=fill, font=font)


def draw_progress_bar(draw: ImageDraw.Draw, x: int, y: int, w: int, h: int,
                      progress: float, color=ACCENT, bg=DARK_GRAY):
    """Draw a progress bar."""
    draw.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=bg)
    if progress > 0:
        fw = max(h, int(w * min(1.0, progress)))
        draw.rounded_rectangle([x, y, x + fw, y + h], radius=h // 2, fill=color)


def draw_glow_circle(img: Image.Image, cx: int, cy: int, radius: int, color, alpha=40):
    """Draw a soft glow circle."""
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for r in range(radius, 0, -2):
        a = int(alpha * (r / radius))
        od.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*color, a))
    img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"))


def draw_nyrqis_logo(draw: ImageDraw.Draw, cx: int, cy: int, size: int, color=ACCENT):
    """Draw the Nyrqis mushroom logo."""
    # Stem
    stem_w = size // 4
    stem_h = size // 2
    draw.rounded_rectangle(
        [cx - stem_w, cy, cx + stem_w, cy + stem_h],
        radius=stem_w // 2, fill=color
    )
    # Cap (arc)
    cap_r = size // 2
    draw.ellipse(
        [cx - cap_r, cy - cap_r, cx + cap_r, cy + cap_r // 3],
        fill=color
    )
    # Eyes
    eye_r = size // 10
    draw.ellipse([cx - cap_r // 2 - eye_r, cy - cap_r // 3,
                   cx - cap_r // 2 + eye_r, cy - cap_r // 3 + eye_r * 2], fill=BG_BLACK)
    draw.ellipse([cx + cap_r // 2 - eye_r, cy - cap_r // 3,
                   cx + cap_r // 2 + eye_r, cy - cap_r // 3 + eye_r * 2], fill=BG_BLACK)


# ---------------------------------------------------------------------------
# Boot phases
# ---------------------------------------------------------------------------

def phase_bios(frame: int, total: int, img: Image.Image):
    """BIOS/firmware splash with Nyrqis logo."""
    draw_gradient_bg(img, BG_BLACK, BG_BOOT)
    t = frame / total

    # Logo fade in
    alpha = min(1.0, t * 2)
    logo_color = tuple(int(c * alpha) for c in ACCENT)
    draw_nyrqis_logo(ImageDraw.Draw(img), W // 2, H // 2 - 100, 120, logo_color)

    # Title fade in
    if t > 0.3:
        title_alpha = min(1.0, (t - 0.3) * 3)
        title_color = tuple(int(c * title_alpha) for c in WHITE)
        draw = ImageDraw.Draw(img)
        draw_centered_text(draw, "NYRQIS", H // 2 + 40, get_bold_font(48), title_color)
        draw_centered_text(draw, "Operating System", H // 2 + 100, get_sans_font(20), GRAY)

    # BIOS text
    if t > 0.6:
        draw = ImageDraw.Draw(img)
        bios_alpha = min(1.0, (t - 0.6) * 2.5)
        bios_color = tuple(int(c * bios_alpha) for c in GRAY)
        draw.text((50, H - 80), "Nyrqis Firmware v0.1.0", fill=bios_color, font=get_font(14))
        draw.text((50, H - 55), "Press F2 for setup", fill=bios_color, font=get_font(14))

    # Bottom progress
    draw = ImageDraw.Draw(img)
    draw_progress_bar(draw, W // 2 - 150, H - 40, 300, 6, t, ACCENT_DIM)


def phase_kernel(frame: int, total: int, img: Image.Image, backend_type: str = "nyrqis"):
    """Kernel loading with progress and log messages."""
    draw_gradient_bg(img, BG_BLACK, BG_BOOT)
    t = frame / total

    draw = ImageDraw.Draw(img)

    # Kernel title
    draw_centered_text(draw, "Nyrqis Kernel", 60, get_bold_font(28), ACCENT)

    # Log messages
    messages = [
        (0.0, f"[    0.000000] Linux version 6.x-nyrqis ({backend_type})"),
        (0.05, "[    0.001234] Command line: BOOT_IMAGE=/boot/vmlinuz-nyrqis"),
        (0.1, "[    0.003456] BIOS-provided physical RAM map:"),
        (0.15, "[    0.004567] BIOS-e820: [mem 0x00000000-0x0009fbff] usable"),
        (0.2, "[    0.010000] NX (Execute Disable) protection: active"),
        (0.25, "[    0.020000] SMBIOS 3.0.0 present"),
        (0.3, f"[    0.050000] Nyrqis kernel: {backend_type} backend detected"),
        (0.35, f"[    0.060000] Initializing {backend_type} display subsystem"),
        (0.4, "[    0.080000] Memory: 16384MB available"),
        (0.45, "[    0.100000] CPU: Nyrqis处理器 @ 3.2GHz"),
        (0.5, "[    0.150000] Loading nyfs root filesystem..."),
        (0.55, "[    0.200000] nyfs: mounted read-write on /dev/nyfs0"),
        (0.6, "[    0.250000] Starting nyrqis-init..."),
        (0.7, "[    0.300000] Mounting virtual filesystems..."),
        (0.8, "[    0.400000] Kernel ready."),
    ]

    y = 120
    for msg_t, msg in messages:
        if t >= msg_t:
            msg_alpha = min(1.0, (t - msg_t) * 10)
            msg_color = tuple(int(c * msg_alpha) for c in GRAY)
            draw.text((60, y), msg, fill=msg_color, font=get_font(13))
            y += 22

    # Progress bar
    draw_progress_bar(draw, 60, H - 60, W - 120, 8, t, GREEN)


def phase_backend_detect(frame: int, total: int, img: Image.Image, backend_type: str = "nyrqis"):
    """Backend detection — show which backend is being used."""
    draw_gradient_bg(img, BG_BLACK, BG_BOOT)
    t = frame / total
    draw = ImageDraw.Draw(img)

    draw_centered_text(draw, "Backend Detection", 80, get_bold_font(28), ACCENT)

    # Detection steps
    steps = [
        (0.0, "Checking environment variables...", f"NYRQIS_BACKEND={backend_type}"),
        (0.2, "Probing /dev/dri...", "Found" if backend_type == "linux" else "Not found"),
        (0.4, "Probing /dev/nyrqis...", "Found" if backend_type == "nyrqis" else "Not found"),
        (0.6, f"Selected backend:", backend_type.upper()),
        (0.8, "Initializing backend...", "OK"),
    ]

    y = 160
    for step_t, label, value in steps:
        if t >= step_t:
            alpha = min(1.0, (t - step_t) * 5)
            lc = tuple(int(c * alpha) for c in WHITE)
            vc = GREEN if "Found" in value or "OK" in value or value == backend_type.upper() else GRAY
            vc = tuple(int(c * alpha) for c in vc)
            draw.text((150, y), label, fill=lc, font=get_font(18))
            draw.text((550, y), value, fill=vc, font=get_bold_font(18))
            y += 50

    # Backend icon
    if t > 0.6:
        icon_alpha = min(1.0, (t - 0.6) * 2.5)
        icon_color = tuple(int(c * icon_alpha) for c in GREEN)
        draw.rounded_rectangle([W // 2 - 120, H - 120, W // 2 + 120, H - 70],
                               radius=10, fill=icon_color)
        draw.text((W // 2 - 60, H - 115), f"{backend_type.upper()} OK", fill=BG_BLACK, font=get_bold_font(20))


def phase_services(frame: int, total: int, img: Image.Image):
    """Service startup — systemd-style."""
    draw_gradient_bg(img, BG_BLACK, BG_BOOT)
    t = frame / total
    draw = ImageDraw.Draw(img)

    draw_centered_text(draw, "Starting Services", 60, get_bold_font(28), ACCENT)

    services = [
        (0.0, "nyrqis-network.service", "started"),
        (0.08, "nyrqis-display.service", "started"),
        (0.16, "nyrqis-input.service", "started"),
        (0.24, "nyrqis-audio.service", "started"),
        (0.32, "nyrqis-gpu.service", "started"),
        (0.40, "nyrqis-compositor.service", "started"),
        (0.48, "nyrqis-shell.service", "started"),
        (0.56, "nyrqis-desktop.service", "started"),
        (0.64, "nyrqis-notifications.service", "started"),
        (0.72, "nyrqis-panel.service", "started"),
        (0.80, "nyrqis-app-launcher.service", "started"),
        (0.88, "nyrqis-wallpaper.service", "started"),
    ]

    y = 120
    for svc_t, name, status in services:
        if t >= svc_t:
            alpha = min(1.0, (t - svc_t) * 8)
            nc = tuple(int(c * alpha) for c in WHITE)
            sc = tuple(int(c * alpha) for c in GREEN)
            icon = "●" if t > svc_t + 0.05 else "○"
            ic_color = GREEN if t > svc_t + 0.05 else GRAY
            ic = tuple(int(c * alpha) for c in ic_color)
            draw.text((80, y), icon, fill=ic, font=get_font(14))
            draw.text((110, y), name, fill=nc, font=get_font(14))
            draw.text((W - 200, y), f"[ {status} ]", fill=sc, font=get_font(14))
            y += 28

    # Overall progress
    active = sum(1 for s_t, _, _ in services if t >= s_t)
    draw_progress_bar(draw, 80, H - 50, W - 160, 8, active / len(services), GREEN)
    draw.text((W - 80, H - 65), f"{active}/{len(services)}", fill=GRAY, font=get_font(12))


def phase_compositor(frame: int, total: int, img: Image.Image):
    """Compositor initialization — surfaces and rendering."""
    draw_gradient_bg(img, BG_BLACK, BG_BOOT)
    t = frame / total
    draw = ImageDraw.Draw(img)

    draw_centered_text(draw, "Compositor Init", 60, get_bold_font(28), ACCENT)

    # Creating surfaces animation
    surfaces = []
    max_surfaces = int(t * 8)
    for i in range(min(max_surfaces, 8)):
        sx = 100 + (i % 4) * 280
        sy = 150 + (i // 4) * 220
        sw, sh = 240, 160
        alpha = min(1.0, (t * 8 - i) * 2) if t * 8 > i else 0
        wc = tuple(int(c * alpha) for c in WINDOW_BG)
        tc = tuple(int(c * alpha) for c in WINDOW_TITLE)
        draw.rounded_rectangle([sx, sy, sx + sw, sy + sh], radius=8, fill=wc, outline=tc, width=2)
        draw.rounded_rectangle([sx, sy, sx + sw, sy + 28], radius=8, fill=tc)
        names = ["Terminal", "Files", "Settings", "Browser", "Editor", "Calendar", "Notes", "Music"]
        draw.text((sx + 10, sy + 5), names[i], fill=GRAY, font=get_font(13))

    # Status
    if t > 0.5:
        status_alpha = min(1.0, (t - 0.5) * 3)
        sc = tuple(int(c * status_alpha) for c in GREEN)
        draw.text((80, H - 100), f"Surfaces: {max_surfaces}/8 created", fill=sc, font=get_font(16))
        draw.text((80, H - 70), "Vulkan renderer: nyrqis-vulkan", fill=GRAY, font=get_font(14))
        draw_progress_bar(draw, 80, H - 40, W - 160, 8, t, ACCENT)


def phase_shell(frame: int, total: int, img: Image.Image):
    """Shell loading — taskbar, clock, wallpaper."""
    t = frame / total
    draw_gradient_bg(img, WALLPAPER_1, WALLPAPER_2)

    draw = ImageDraw.Draw(img)

    # Wallpaper gradient effect
    for y in range(0, H, 3):
        wave = int(10 * math.sin(y / 40 + t * 5))
        color = (20 + wave, 20 + wave, 38 + wave)
        draw.line([(0, y), (W, y)], fill=color)

    # Shell elements fade in
    if t > 0.2:
        alpha = min(1.0, (t - 0.2) * 2)
        tc = tuple(int(c * alpha) for c in TASKBAR_BG)
        draw.rectangle([0, H - 48, W, H], fill=tc)

        # Clock
        clock_alpha = min(1.0, (t - 0.3) * 3)
        cc = tuple(int(c * clock_alpha) for c in WHITE)
        draw.text((W // 2 - 50, H - 35), "12:00", fill=cc, font=get_bold_font(20))

        # App icons in taskbar
        if t > 0.4:
            icons = ["🖥", "📁", "⚙️", "🌐", "📝"]
            for i, icon in enumerate(icons):
                ix = 20 + i * 50
                draw.text((ix, H - 42), icon, fill=WHITE, font=get_font(18))

    # Title
    if t > 0.6:
        alpha = min(1.0, (t - 0.6) * 2.5)
        tc = tuple(int(c * alpha) for c in WHITE)
        draw_centered_text(draw, "Nyrqis Shell Loaded", H // 2 - 20, get_bold_font(32), tc)


def phase_desktop(frame: int, total: int, img: Image.Image):
    """Desktop ready — full UI with windows."""
    t = frame / total

    # Wallpaper
    draw_gradient_bg(img, WALLPAPER_1, WALLPAPER_2)
    draw = ImageDraw.Draw(img)

    # Wallpaper pattern
    for y in range(0, H, 3):
        wave = int(10 * math.sin(y / 40 + 2))
        color = (20 + wave, 20 + wave, 38 + wave)
        draw.line([(0, y), (W, y)], fill=color)

    # Windows
    windows = [
        (80, 60, 520, 420, "Terminal", ["nyrqis@desktop:~$", "$ neofetch", "OS: Nyrqis 0.1.0"]),
        (200, 120, 640, 480, "Files", ["📁 /home/user", "   Documents/", "   Downloads/"]),
        (350, 80, 780, 440, "Settings", ["⚙️ Display", "   Theme: Dark", "   Resolution: 1920x1080"]),
    ]

    for i, (wx, wy, wx2, wy2, title, content_lines) in enumerate(windows):
        # Window fade in
        w_alpha = min(1.0, t * 3 - i * 0.3)
        if w_alpha <= 0:
            continue
        wc = tuple(int(c * w_alpha) for c in WINDOW_BG)
        tc = tuple(int(c * w_alpha) for c in WINDOW_TITLE)

        # Window shadow
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sd = ImageDraw.Draw(shadow)
        sd.rounded_rectangle([wx + 4, wy + 4, wx2 + 4, wy2 + 4], radius=10, fill=(0, 0, 0, 40))
        img.paste(Image.alpha_composite(img.convert("RGBA"), shadow).convert("RGB"))

        draw.rounded_rectangle([wx, wy, wx2, wy2], radius=10, fill=wc, outline=tc, width=2)
        draw.rounded_rectangle([wx, wy, wx2, wy + 32], radius=10, fill=tc)
        draw.text((wx + 12, wy + 7), title, fill=GRAY, font=get_font(14))

        # Close/minimize/maximize dots
        for j, c in enumerate([(220, 70, 70), (220, 180, 60), (60, 200, 100)]):
            dot_c = tuple(int(v * w_alpha) for v in c)
            draw.ellipse([wx + 12 + j * 22, wy + 9, wx + 24 + j * 22, wy + 21], fill=dot_c)

        # Content lines
        for li, line_text in enumerate(content_lines):
            cc_color = GREEN if li == 0 else WHITE
            cc = tuple(int(c * w_alpha) for c in cc_color)
            draw.text((wx + 15, wy + 42 + li * 22), line_text, fill=cc, font=get_font(14))

    # Taskbar
    draw.rectangle([0, H - 48, W, H], fill=TASKBAR_BG)

    # Taskbar icons
    icons = ["🖥", "📁", "⚙️", "🌐", "📝"]
    for i, icon in enumerate(icons):
        draw.text((20 + i * 50, H - 42), icon, fill=WHITE, font=get_font(18))

    # Clock
    draw.text((W - 120, H - 35), "12:00", fill=WHITE, font=get_bold_font(18))
    draw.text((W - 120, H - 15), "Sep 5", fill=GRAY, font=get_font(12))

    # "Desktop ready" notification
    if t > 0.7:
        na = min(1.0, (t - 0.7) * 3)
        nc = tuple(int(c * na) for c in GREEN)
        draw.rounded_rectangle([W - 340, 20, W - 20, 70], radius=10, fill=(20, 20, 38), outline=nc, width=1)
        draw.text((W - 330, 28), "✓ Desktop Ready", fill=nc, font=get_bold_font(16))
        draw.text((W - 330, 48), "Nyrqis OS v0.1.0", fill=GRAY, font=get_font(12))


# ---------------------------------------------------------------------------
# Main render loop
# ---------------------------------------------------------------------------

def render_boot_sequence(backend_type: str = "nyrqis", num_frames: int = 120, output_dir: str = "/tmp/nyrqis_boot_full"):
    """Render the complete boot sequence."""
    os.makedirs(output_dir, exist_ok=True)

    # Phase allocation (frames per phase)
    phases = [
        ("BIOS Splash", int(num_frames * 0.12), phase_bios),
        ("Kernel Loading", int(num_frames * 0.18), phase_kernel),
        ("Backend Detection", int(num_frames * 0.10), phase_backend_detect),
        ("Service Startup", int(num_frames * 0.15), phase_services),
        ("Compositor Init", int(num_frames * 0.15), phase_compositor),
        ("Shell Loading", int(num_frames * 0.12), phase_shell),
        ("Desktop Ready", int(num_frames * 0.18), phase_desktop),
    ]

    frame_num = 0
    for phase_name, phase_frames, phase_func in phases:
        print(f"  Phase: {phase_name} ({phase_frames} frames)")
        for i in range(phase_frames):
            img = Image.new("RGB", (W, H), BG_BLACK)
            if phase_func == phase_bios:
                phase_func(i, phase_frames, img)
            elif phase_func == phase_kernel:
                phase_func(i, phase_frames, img, backend_type)
            elif phase_func == phase_backend_detect:
                phase_func(i, phase_frames, img, backend_type)
            else:
                phase_func(i, phase_frames, img)

            frame_path = os.path.join(output_dir, f"frame_{frame_num:04d}.png")
            img.save(frame_path)
            frame_num += 1

    print(f"\n  Rendered: {frame_num} frames → {output_dir}")

    # Create GIF
    gif_path = os.path.join(output_dir, "boot_animation.gif")
    frames = []
    for i in range(frame_num):
        frames.append(Image.open(os.path.join(output_dir, f"frame_{i:04d}.png")))

    duration_ms = int(1000 / 12.5)  # 12.5 fps
    frames[0].save(
        gif_path,
        save_all=True,
        append_images=frames[1:],
        duration=duration_ms,
        loop=0,
        optimize=True,
    )
    gif_size = os.path.getsize(gif_path)
    duration_s = frame_num * duration_ms / 1000
    print(f"  GIF: {gif_path} ({gif_size // 1024}KB)")
    print(f"  Duration: ~{duration_s:.1f}s at {1000 / duration_ms:.1f}fps")
    print(f"\n  Done! 🍄")
    return gif_path


def main():
    parser = argparse.ArgumentParser(description="Nyrqis OS Boot Animation")
    parser.add_argument("--backend", choices=["nyrqis", "linux", "headless"],
                        default="nyrqis", help="Backend type to simulate")
    parser.add_argument("--frames", type=int, default=120, help="Total frames")
    parser.add_argument("--output", default="/tmp/nyrqis_boot_full", help="Output directory")
    args = parser.parse_args()

    print("╔══════════════════════════════════════════════╗")
    print("║    Nyrqis OS — Full Boot Animation           ║")
    print(f"║    Backend: {args.backend:<32} ║")
    print("╚══════════════════════════════════════════════╝\n")

    render_boot_sequence(args.backend, args.frames, args.output)


if __name__ == "__main__":
    main()
