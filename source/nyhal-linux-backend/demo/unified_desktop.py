#!/usr/bin/env python3
"""Unified Desktop Demo — All components wired together.

Renders a complete Nyrqis desktop scene with:
- Desktop panel (taskbar, system tray, clock)
- Terminal window
- File manager window
- Settings window (display/audio)
- Notification shade (pull from top-left)
- Quick settings (pull from top-right)
- App launcher
- Spotlight search
- System monitor
- Context menu
- Power menu
- Desktop wallpaper

Usage:
    python3 demo/unified_desktop.py --output /tmp/unified-desktop
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def render_frame(
    width: int = 1920,
    height: int = 1080,
    state: str = "desktop",
    output_path: str = "/tmp/unified-desktop",
) -> str:
    """Render a single desktop frame.

    Parameters
    ----------
    state : str
        One of: desktop, notifications, quick_settings, launcher,
        spotlight, monitor, context_menu, power, display_settings,
        audio_mixer
    """
    if not HAS_PIL:
        raise RuntimeError("PIL (Pillow) is required: pip install Pillow")

    img = Image.new("RGB", (width, height), (20, 22, 36))
    draw = ImageDraw.Draw(img)

    # Fonts
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
        font_bold = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
        font_title = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
        font_large = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
        font_small = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
    except (OSError, IOError):
        font = font_bold = font_title = font_large = font_small = ImageFont.load_default()

    # === Wallpaper gradient ===
    for y in range(height):
        t = y / height
        r = int(20 + t * 30)
        g = int(22 + t * 28)
        b = int(36 + t * 32)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    # === Desktop panel (bottom bar) ===
    panel_h = 48
    panel_y = height - panel_h
    draw.rectangle([0, panel_y, width, height], fill=(30, 30, 40, 240))
    draw.line([(0, panel_y), (width, panel_y)], fill=(60, 60, 80))

    # Start button
    draw.ellipse([12, panel_y + 8, 40, panel_y + 36], fill=(80, 140, 255))

    # Pinned apps
    pinned_colors = [(60, 200, 120), (255, 200, 60), (80, 140, 255), (180, 180, 200)]
    for i, color in enumerate(pinned_colors):
        x = 48 + i * 38
        draw.rectangle([x, panel_y + 10, x + 28, panel_y + 38], fill=color)

    # Divider
    draw.line([(200, panel_y + 12), (200, panel_y + 36)], fill=(60, 60, 80))

    # Running apps
    draw.rectangle([216, panel_y + 10, 244, panel_y + 38], fill=(50, 50, 70))
    draw.rectangle([224, panel_y + 16, 236, panel_y + 28], fill=(60, 200, 120))
    draw.rectangle([216, panel_y + 40, 244, panel_y + 44], fill=(80, 140, 255))

    draw.rectangle([260, panel_y + 10, 288, panel_y + 38], fill=(50, 50, 70))
    draw.rectangle([268, panel_y + 16, 280, panel_y + 28], fill=(255, 200, 60))

    draw.rectangle([304, panel_y + 10, 332, panel_y + 38], fill=(50, 50, 70))
    draw.rectangle([312, panel_y + 16, 324, panel_y + 28], fill=(180, 180, 200))

    # Clock (right side)
    now = time.localtime()
    time_str = f"{now.tm_hour:02d}:{now.tm_min:02d}"
    date_str = f"{now.tm_mday:02d}/{now.tm_mon:02d}"
    draw.rectangle([width - 140, panel_y + 8, width - 68, panel_y + 40], fill=(50, 50, 68))
    draw.rectangle([width - 60, panel_y + 8, width - 12, panel_y + 40], fill=(50, 50, 68))

    # System tray icons
    tray_x = width - 180
    draw.rectangle([tray_x, panel_y + 14, tray_x + 16, panel_y + 30], fill=(80, 200, 120))
    draw.rectangle([tray_x + 24, panel_y + 14, tray_x + 40, panel_y + 30], fill=(180, 180, 200))
    draw.rectangle([tray_x + 48, panel_y + 14, tray_x + 64, panel_y + 30], fill=(80, 200, 120))

    # Workspace dots
    for i in range(4):
        cx = width - 200 + i * 14
        color = (80, 140, 255) if i == 0 else (80, 80, 100)
        draw.ellipse([cx - 4, panel_y + 22, cx + 4, panel_y + 30], fill=color)

    # === Terminal window ===
    if state in ("desktop", "launcher", "spotlight", "power"):
        win_x, win_y = 60, 60
        win_w, win_h = 640, 420

        # Shadow
        draw.rectangle([win_x + 4, win_y + 4, win_x + win_w + 4, win_y + win_h + 4],
                       fill=(10, 10, 15))
        # Window frame
        draw.rectangle([win_x, win_y, win_x + win_w, win_y + win_h],
                       fill=(35, 35, 48), outline=(60, 60, 80))
        # Title bar
        draw.rectangle([win_x, win_y, win_x + win_w, win_y + 32], fill=(42, 42, 56))
        # Traffic lights
        draw.ellipse([win_x + 10, win_y + 10, win_x + 20, win_y + 20], fill=(255, 95, 86))
        draw.ellipse([win_x + 26, win_y + 10, win_x + 36, win_y + 20], fill=(255, 189, 46))
        draw.ellipse([win_x + 42, win_y + 10, win_x + 52, win_y + 20], fill=(39, 201, 63))

        # Terminal content
        terminal_lines = [
            "$ uname -a",
            "Nyrqis 6.2.0-nyrqis #1 SMP x86_64 GNU/Linux",
            "",
            "$ neofetch",
            "       ▄▄▄▄▄▄▄       zeus@nyrqis",
            "      █░░░░░░░█      OS: Nyrqis 6.2.0",
            "     █░░█░█░░░█     Host: Myco Framework",
            "    █░░░░░░░░░░█    Kernel: 6.2.0-nyrqis",
            "   █░░░░░░░░░░░█    Shell: nyrqis-shell 1.0",
            "    █░░░░░░░░░█     Compositor: nyrqis-compositor",
            "     █░░░░░░░█      Theme: Eclipse Dark",
            "      █░░░░░█       Terminal: nyrqis-terminal",
            "       █▄▄▄█        Memory: 4.2 GiB / 16 GiB",
            "",
            "$ glxinfo | grep 'OpenGL version'",
            "OpenGL version string: 4.6.0 NVIDIA 535.129.03",
            "",
            "$ vulkaninfo --summary",
            "GPU 0: NVIDIA GeForce RTX 4070",
            "API Version: 1.3.250",
            "",
            "$ ▌",
        ]
        ty = win_y + 40
        for line in terminal_lines:
            color = (60, 200, 120) if line.startswith("$") else (
                (150, 150, 170) if not line else (200, 200, 210))
            draw.text((win_x + 12, ty), line, fill=color, font=font)
            ty += 18
            if ty > win_y + win_h - 20:
                break

    # === File manager window ===
    if state in ("desktop", "launcher", "spotlight", "power"):
        fm_x, fm_y = 720, 60
        fm_w, fm_h = 480, 420

        draw.rectangle([fm_x + 4, fm_y + 4, fm_x + fm_w + 4, fm_y + fm_h + 4],
                       fill=(10, 10, 15))
        draw.rectangle([fm_x, fm_y, fm_x + fm_w, fm_y + fm_h],
                       fill=(35, 35, 48), outline=(60, 60, 80))
        draw.rectangle([fm_x, fm_y, fm_x + fm_w, fm_y + 32], fill=(42, 42, 56))
        draw.ellipse([fm_x + 10, fm_y + 10, fm_x + 20, fm_y + 20], fill=(255, 95, 86))
        draw.ellipse([fm_x + 26, fm_y + 10, fm_x + 36, fm_y + 20], fill=(255, 189, 46))
        draw.ellipse([fm_x + 42, fm_y + 10, fm_x + 52, fm_y + 20], fill=(39, 201, 63))

        # Breadcrumb bar
        draw.rectangle([fm_x, fm_y + 32, fm_x + fm_w, fm_y + 56], fill=(40, 40, 54))

        # File list
        files = [
            ("📁", "Documents", (255, 200, 60), "12 items"),
            ("📁", "Downloads", (255, 200, 60), "5 items"),
            ("📁", "Pictures", (255, 200, 60), "128 items"),
            ("📁", "Music", (255, 200, 60), "340 items"),
            ("📁", "Videos", (255, 200, 60), "12 items"),
            ("📄", "readme.md", (180, 180, 200), "4.2 KB"),
            ("📄", "config.json", (80, 140, 255), "1.1 KB"),
            ("🖼️", "wallpaper.png", (180, 180, 200), "2.4 MB"),
            ("📦", "archive.tar.gz", (180, 180, 200), "156 MB"),
            ("🐍", "main.py", (60, 200, 120), "8.7 KB"),
            ("⚙️", "Makefile", (180, 180, 200), "2.1 KB"),
            ("📁", ".config", (255, 200, 60), "8 items"),
        ]
        fy = fm_y + 60
        for i, (icon, name, color, size) in enumerate(files):
            if i % 2 == 0:
                draw.rectangle([fm_x + 1, fy, fm_x + fm_w - 1, fy + 28],
                              fill=(38, 38, 52))
            draw.rectangle([fm_x + 12, fy + 6, fm_x + 24, fy + 18], fill=color)
            draw.text((fm_x + 32, fy + 6), name, fill=(200, 200, 210), font=font)
            draw.text((fm_x + fm_w - 80, fy + 6), size, fill=(120, 120, 140), font=font_small)
            fy += 28

    # === Settings window ===
    if state in ("desktop", "display_settings", "audio_mixer"):
        sx, sy = 1220, 120
        sw, sh = 420, 500

        draw.rectangle([sx + 4, sy + 4, sx + sw + 4, sy + sh + 4], fill=(10, 10, 15))
        draw.rectangle([sx, sy, sx + sw, sy + sh], fill=(35, 35, 48), outline=(60, 60, 80))
        draw.rectangle([sx, sy, sx + sw, sy + 32], fill=(42, 42, 56))
        draw.ellipse([sx + 10, sy + 10, sx + 20, sy + 20], fill=(255, 95, 86))
        draw.ellipse([sx + 26, sy + 10, sx + 36, sy + 20], fill=(255, 189, 46))
        draw.ellipse([sx + 42, sy + 10, sx + 52, sy + 20], fill=(39, 201, 63))

        if state == "display_settings":
            # Display settings content
            draw.text((sx + 16, sy + 44), "Display Settings", fill=(230, 230, 240), font=font_title)

            # Resolution info
            draw.text((sx + 16, sy + 80), "Resolution", fill=(150, 150, 170), font=font)
            draw.rectangle([sx + 16, sy + 98, sx + sw - 16, sy + 126], fill=(42, 42, 56))
            draw.text((sx + 24, sy + 104), "1920 × 1080 @ 60Hz", fill=(200, 200, 210), font=font)

            # Scaling
            draw.text((sx + 16, sy + 144), "Scale", fill=(150, 150, 170), font=font)
            draw.rectangle([sx + 16, sy + 162, sx + sw - 16, sy + 190], fill=(42, 42, 56))
            draw.text((sx + 24, sy + 168), "100%", fill=(200, 200, 210), font=font)

            # Wallpaper preview
            draw.text((sx + 16, sy + 210), "Wallpaper", fill=(150, 150, 170), font=font)
            # Gradient preview
            for dy in range(80):
                t = dy / 80
                r = int(20 + t * 30)
                g = int(22 + t * 28)
                b = int(36 + t * 32)
                draw.line([(sx + 16, sy + 228 + dy), (sx + sw - 16, sy + 228 + dy)],
                         fill=(r, g, b))

            # Night light toggle
            draw.text((sx + 16, sy + 320), "Night Light", fill=(150, 150, 170), font=font)
            draw.rectangle([sx + sw - 60, sy + 318, sx + sw - 20, sy + 336], fill=(50, 50, 65))
            draw.ellipse([sx + sw - 58, sy + 320, sx + sw - 42, sy + 334], fill=(100, 100, 120))

        elif state == "audio_mixer":
            draw.text((sx + 16, sy + 44), "Audio", fill=(230, 230, 240), font=font_title)

            # Master volume
            draw.text((sx + 16, sy + 80), "Master Volume", fill=(150, 150, 170), font=font)
            draw.rectangle([sx + 16, sy + 98, sx + sw - 16, sy + 118], fill=(50, 50, 65))
            draw.rectangle([sx + 16, sy + 98, sx + 16 + int((sw - 32) * 0.8), sy + 118],
                          fill=(80, 200, 120))

            # App streams
            apps = [("Firefox", (255, 120, 60), 70), ("Spotify", (30, 215, 96), 85)]
            ay = sy + 140
            for app_name, color, vol in apps:
                draw.rectangle([sx + 16, ay, sx + 32, ay + 16], fill=color)
                draw.text((sx + 40, ay), app_name, fill=(200, 200, 210), font=font)
                draw.rectangle([sx + 16, ay + 22, sx + sw - 60, ay + 34], fill=(50, 50, 65))
                draw.rectangle([sx + 16, ay + 22, sx + 16 + int((sw - 76) * vol / 100), ay + 34],
                              fill=(80, 140, 255))
                ay += 56

        else:
            # General settings
            draw.text((sx + 16, sy + 44), "Settings", fill=(230, 230, 240), font=font_title)
            settings_items = [
                ("Display", (80, 140, 255)),
                ("Audio", (80, 200, 120)),
                ("Network", (255, 200, 60)),
                ("Bluetooth", (80, 140, 255)),
                ("Battery", (80, 200, 120)),
                ("Keyboard", (180, 180, 200)),
                ("Mouse", (180, 180, 200)),
                ("Accessibility", (255, 200, 60)),
                ("Theme", (180, 180, 200)),
                ("About", (180, 180, 200)),
            ]
            iy = sy + 80
            for name, color in settings_items:
                draw.rectangle([sx + 16, iy + 2, sx + 28, iy + 14], fill=color)
                draw.text((sx + 36, iy), name, fill=(200, 200, 210), font=font)
                iy += 28

    # === State-specific overlays ===

    if state == "notifications":
        # Notification shade (pulled from top-left)
        shade_w = 380
        draw.rectangle([0, 0, shade_w, height - panel_h], fill=(36, 36, 50, 240))

        # Notifications
        notifs = [
            ("Terminal", "Process completed", (60, 200, 120)),
            ("System", "Update available", (80, 140, 255)),
            ("Files", "Download complete", (255, 200, 60)),
        ]
        ny = 16
        for title, body, color in notifs:
            draw.rectangle([12, ny, shade_w - 12, ny + 64], fill=(46, 46, 62), outline=(60, 60, 80))
            draw.rectangle([20, ny + 8, 36, ny + 24], fill=color)
            draw.text((44, ny + 8), title, fill=(230, 230, 240), font=font_bold)
            draw.text((44, ny + 28), body, fill=(150, 150, 170), font=font_small)
            ny += 76

        # Quick toggles at bottom
        toggles = [("WiFi", (80, 200, 120)), ("BT", (80, 140, 255)),
                   ("🔦", (180, 180, 200)), ("✈️", (180, 180, 200))]
        tx = 16
        for label, color in toggles:
            draw.rectangle([tx, height - panel_h - 70, tx + 56, height - panel_h - 14],
                          fill=(50, 50, 65), outline=(60, 60, 80))
            draw.rectangle([tx + 20, height - panel_h - 58, tx + 36, height - panel_h - 42],
                          fill=color)
            tx += 68

    elif state == "quick_settings":
        # Quick settings (pulled from top-right)
        qs_x = width - 380
        draw.rectangle([qs_x, 0, width, height - panel_h], fill=(36, 36, 50, 240))

        # Brightness slider
        draw.text((qs_x + 16, 20), "Brightness", fill=(200, 200, 210), font=font)
        draw.rectangle([qs_x + 16, 42, width - 16, 58], fill=(50, 50, 65))
        draw.rectangle([qs_x + 16, 42, qs_x + 16 + int(348 * 0.75), 58],
                      fill=(255, 200, 60))

        # Volume slider
        draw.text((qs_x + 16, 74), "Volume", fill=(200, 200, 210), font=font)
        draw.rectangle([qs_x + 16, 96, width - 16, 112], fill=(50, 50, 65))
        draw.rectangle([qs_x + 16, 96, qs_x + 16 + int(348 * 0.65), 112],
                      fill=(80, 200, 120))

        # Toggle grid
        grid_items = [
            ("WiFi", True, (80, 200, 120)),
            ("Bluetooth", False, (80, 80, 100)),
            ("Airplane", False, (80, 80, 100)),
            ("Flashlight", False, (80, 80, 100)),
            ("DND", True, (255, 80, 80)),
            ("Night Light", False, (80, 80, 100)),
        ]
        gx, gy = qs_x + 16, 130
        for i, (label, active, color) in enumerate(grid_items):
            col = i % 3
            row = i // 3
            bx = gx + col * 114
            by = gy + row * 60
            bg = (50, 50, 70) if active else (42, 42, 56)
            draw.rounded_rectangle([bx, by, bx + 106, by + 52], radius=8, fill=bg)
            draw.rectangle([bx + 40, by + 8, bx + 66, by + 24], fill=color)

        # Brightness icon
        draw.text((qs_x + 16, 260), "☀️", fill=(255, 200, 60), font=font)
        # Volume icon
        draw.text((qs_x + 16, 310), "🔊", fill=(180, 180, 200), font=font)

    elif state == "launcher":
        # App launcher overlay
        draw.rectangle([0, 0, width, height], fill=(20, 20, 30, 200))

        # Search bar
        search_w = 500
        search_x = (width - search_w) // 2
        draw.rounded_rectangle([search_x, 80, search_x + search_w, 120],
                              radius=12, fill=(42, 42, 56), outline=(80, 140, 255))
        draw.text((search_x + 16, 92), "🔍  Search apps...", fill=(120, 120, 140), font=font)

        # App grid
        apps = [
            ("Terminal", (60, 200, 120)), ("Files", (255, 200, 60)),
            ("Browser", (80, 140, 255)), ("Settings", (180, 180, 200)),
            ("Calculator", (80, 140, 255)), ("Text Editor", (60, 200, 120)),
            ("Monitor", (255, 200, 60)), ("Log Viewer", (180, 180, 200)),
            ("Screenshot", (80, 200, 120)), ("Service Mgr", (180, 180, 200)),
            ("Package Mgr", (80, 140, 255)), ("Power", (255, 80, 80)),
        ]
        cols = 4
        cell_w = 120
        cell_h = 100
        start_x = (width - cols * cell_w) // 2
        for i, (name, color) in enumerate(apps):
            col = i % cols
            row = i // cols
            ax = start_x + col * cell_w
            ay = 150 + row * cell_h
            draw.rounded_rectangle([ax + 8, ay + 8, ax + cell_w - 8, ay + cell_h - 8],
                                  radius=12, fill=(42, 42, 56))
            # App icon
            draw.rounded_rectangle([ax + 32, ay + 16, ax + cell_w - 32, ay + 52],
                                  radius=8, fill=color)
            draw.text((ax + 16, ay + 64), name, fill=(200, 200, 210), font=font_small)

    elif state == "spotlight":
        # Spotlight search
        draw.rectangle([0, 0, width, height], fill=(20, 20, 30, 180))
        sw = 600
        sx_spot = (width - sw) // 2
        draw.rounded_rectangle([sx_spot, 180, sx_spot + sw, 240],
                              radius=12, fill=(42, 42, 56), outline=(80, 140, 255))
        draw.text((sx_spot + 20, 196), "🔍  terminal", fill=(230, 230, 240), font=font)

        # Results
        results = [
            ("Terminal", "Application", (60, 200, 120)),
            ("Terminal Emulator", "System", (180, 180, 200)),
            ("~/.config/terminal.conf", "File", (255, 200, 60)),
        ]
        ry = 250
        for name, category, color in results:
            draw.rounded_rectangle([sx_spot, ry, sx_spot + sw, ry + 40],
                                  radius=8, fill=(46, 46, 62))
            draw.rectangle([sx_spot + 12, ry + 12, sx_spot + 24, ry + 28], fill=color)
            draw.text((sx_spot + 32, ry + 10), name, fill=(230, 230, 240), font=font)
            draw.text((sx_spot + sw - 80, ry + 12), category, fill=(120, 120, 140), font=font_small)
            ry += 48

    elif state == "monitor":
        # System monitor
        draw.rectangle([0, 0, width, height - panel_h], fill=(30, 30, 40))

        # CPU
        draw.text((40, 40), "CPU", fill=(230, 230, 240), font=font_title)
        draw.rectangle([40, 70, width - 40, 90], fill=(50, 50, 65))
        draw.rectangle([40, 70, 40 + int((width - 80) * 0.35), 90], fill=(80, 200, 120))
        draw.text((width - 120, 72), "35%", fill=(200, 200, 210), font=font)

        # Memory
        draw.text((40, 110), "Memory", fill=(230, 230, 240), font=font_title)
        draw.rectangle([40, 140, width - 40, 160], fill=(50, 50, 65))
        draw.rectangle([40, 140, 40 + int((width - 80) * 0.52), 160], fill=(80, 140, 255))
        draw.text((width - 140, 142), "8.3 / 16 GB", fill=(200, 200, 210), font=font)

        # Disk
        draw.text((40, 180), "Disk", fill=(230, 230, 240), font=font_title)
        draw.rectangle([40, 210, width - 40, 230], fill=(50, 50, 65))
        draw.rectangle([40, 210, 40 + int((width - 80) * 0.28), 230], fill=(255, 200, 60))
        draw.text((width - 160, 212), "112 / 400 GB", fill=(200, 200, 210), font=font)

        # Network
        draw.text((40, 250), "Network", fill=(230, 230, 240), font=font_title)
        draw.rectangle([40, 280, width - 40, 300], fill=(50, 50, 65))
        draw.rectangle([40, 280, 40 + int((width - 80) * 0.65), 300], fill=(80, 200, 120))
        draw.text((width - 120, 282), "65%", fill=(200, 200, 210), font=font)

        # Process list
        draw.text((40, 320), "Top Processes", fill=(230, 230, 240), font=font_bold)
        procs = [
            ("firefox", "2.1%", "1.2 GB"),
            ("nyrqis-compositor", "1.8%", "856 MB"),
            ("spotify", "1.2%", "642 MB"),
            ("python3", "0.8%", "234 MB"),
            ("nyrqis-shell", "0.5%", "128 MB"),
        ]
        py = 345
        for name, cpu, mem in procs:
            draw.text((50, py), name, fill=(200, 200, 210), font=font)
            draw.text((250, py), cpu, fill=(80, 200, 120), font=font)
            draw.text((350, py), mem, fill=(80, 140, 255), font=font)
            py += 22

    elif state == "context_menu":
        # Right-click context menu
        draw.rectangle([0, 0, width, height], fill=(0, 0, 0, 50))
        menu_x, menu_y = 400, 300
        menu_w = 220
        items = [
            ("New Window", None),
            ("New Tab", None),
            ("---", None),
            ("Copy", "Ctrl+C"),
            ("Paste", "Ctrl+V"),
            ("Cut", "Ctrl+X"),
            ("---", None),
            ("Select All", "Ctrl+A"),
            ("---", None),
            ("Settings", None),
            ("About", None),
        ]
        iy = menu_y
        for label, shortcut in items:
            if label == "---":
                draw.line([(menu_x + 8, iy + 4), (menu_x + menu_w - 8, iy + 4)],
                         fill=(60, 60, 80))
                iy += 12
            else:
                draw.rectangle([menu_x, iy, menu_x + menu_w, iy + 32], fill=(42, 42, 56))
                draw.text((menu_x + 12, iy + 8), label, fill=(230, 230, 240), font=font)
                if shortcut:
                    draw.text((menu_x + menu_w - 80, iy + 8), shortcut,
                             fill=(120, 120, 140), font=font_small)
                iy += 32

    elif state == "power":
        # Power menu
        draw.rectangle([0, 0, width, height], fill=(0, 0, 0, 150))
        box_w, box_h = 320, 360
        bx = (width - box_w) // 2
        by = (height - box_h) // 2
        draw.rounded_rectangle([bx, by, bx + box_w, by + box_h],
                              radius=16, fill=(40, 40, 40), outline=(80, 80, 80))
        draw.text((bx + 120, by + 16), "Power", fill=(230, 230, 230), font=font_title)

        options = [
            ("Lock", "Lock the screen", (180, 180, 200)),
            ("Log Out", "End your session", (180, 180, 200)),
            ("Sleep", "Suspend to RAM", (180, 180, 200)),
            ("Restart", "Restart the system", (255, 200, 60)),
            ("Shut Down", "Turn off the system", (255, 80, 80)),
        ]
        oy = by + 50
        for i, (label, desc, color) in enumerate(options):
            is_selected = (i == 0)
            if is_selected:
                draw.rounded_rectangle([bx + 12, oy, bx + box_w - 12, oy + 48],
                                      radius=10, fill=(60, 80, 120))
            draw.rectangle([bx + 24, oy + 12, bx + 36, oy + 24], fill=color)
            draw.text((bx + 48, oy + 8), label, fill=(230, 230, 230), font=font)
            draw.text((bx + 48, oy + 28), desc, fill=(130, 130, 130), font=font_small)
            oy += 56

    return img


def main():
    parser = argparse.ArgumentParser(description="Nyrqis Unified Desktop Demo")
    parser.add_argument("--output", default="/tmp/unified-desktop",
                       help="Output directory")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    states = [
        ("desktop", "Full desktop with windows"),
        ("notifications", "Notification shade (top-left)"),
        ("quick_settings", "Quick settings (top-right)"),
        ("launcher", "App launcher"),
        ("spotlight", "Spotlight search"),
        ("monitor", "System monitor"),
        ("context_menu", "Right-click context menu"),
        ("power", "Power menu"),
        ("display_settings", "Display settings"),
        ("audio_mixer", "Audio mixer"),
    ]

    for i, (state, desc) in enumerate(states):
        img = render_frame(args.width, args.height, state)
        path = os.path.join(args.output, f"{i + 1:02d}_{state}.png")
        img.save(path)
        print(f"  [{i + 1:02d}/10] {desc}: {path}")

    print(f"\nAll 10 states rendered to {args.output}/")


if __name__ == "__main__":
    main()
