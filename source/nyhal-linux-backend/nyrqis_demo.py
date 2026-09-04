#!/usr/bin/env python3
"""
Nyrqis OS — Live Desktop Demo

Renders a complete desktop environment with multiple states:
1. Default desktop with windows
2. Terminal focused
3. File manager focused
4. Notification shade (pull from top-left)
5. Quick settings (pull from top-right)
6. Settings panel
7. App launcher

Usage:
    python3 nyrqis_demo.py                    # render all states to /tmp/nyrqis_demo/
    python3 nyrqis_demo.py --state terminal   # render specific state
    python3 nyrqis_demo.py --backend headless  # force backend
    python3 nyrqis_demo.py --live              # interactive demo (requires display)
"""

import argparse
import os
import sys
import time
from typing import Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from ui.backend_abstraction import BackendType, get_backend
from ui.desktop_backend import create_desktop, DesktopBackend


# ---------------------------------------------------------------------------
# Desktop States
# ---------------------------------------------------------------------------

def render_default_desktop(desktop: DesktopBackend) -> Optional[object]:
    """Render the default desktop with multiple windows."""
    desktop.create_window("Terminal", 900, 600)
    desktop.create_window("File Manager", 800, 600)
    desktop.create_window("Settings", 700, 500)
    desktop.focus_window(desktop.state.windows[0].id)
    return desktop.render_to_image()


def render_terminal_focused(desktop: DesktopBackend) -> Optional[object]:
    """Render with terminal window focused and maximized."""
    win = desktop.create_window("Terminal — bash", 1200, 700)
    desktop.maximize_window(win.id)
    desktop.focus_window(win.id)
    return desktop.render_to_image()


def render_file_manager(desktop: DesktopBackend) -> Optional[object]:
    """Render with file manager focused."""
    desktop.create_window("Terminal", 900, 600)
    fm = desktop.create_window("Files — /home/user", 1000, 650)
    desktop.focus_window(fm.id)
    return desktop.render_to_image()


def render_notification_shade(desktop: DesktopBackend) -> Optional[object]:
    """Render the notification shade pulled from top-left."""
    desktop.create_window("Terminal", 900, 600)
    desktop.state.notification_shade_open = True
    img = desktop.render_to_image()

    # Draw notification shade overlay
    if img and hasattr(img, 'draw'):
        try:
            from PIL import ImageDraw, ImageFont
            draw = ImageDraw.Draw(img)

            # Shade background
            draw.rectangle([0, 0, 450, desktop._height], fill=(25, 25, 40, 230))

            # Notifications header
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
                sfont = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
            except (OSError, IOError):
                font = ImageFont.load_default()
                sfont = font

            draw.text((20, 20), "Notifications", fill=(200, 200, 220), font=font)

            # Notification items
            notifications = [
                ("🔵 System Update", "3 security updates available", "2 min ago"),
                ("🟢 New Message", "Alice: Hey, are you free?", "5 min ago"),
                ("🟡 Low Battery", "Battery at 15%. Plug in.", "10 min ago"),
                ("⚪ Build Complete", "nyrqis-backend compiled successfully", "15 min ago"),
                ("🔵 Git Push", "main: 3 commits pushed to origin", "20 min ago"),
            ]

            y = 60
            for icon_text, detail, time_text in notifications:
                draw.rectangle([10, y, 440, y + 70], fill=(35, 35, 55), outline=(50, 50, 70))
                draw.text((20, y + 8), icon_text, fill=(200, 200, 220), font=sfont)
                draw.text((20, y + 30), detail, fill=(140, 140, 160), font=sfont)
                draw.text((350, y + 50), time_text, fill=(100, 100, 120), font=sfont)
                y += 80

            # Clear all button
            draw.rectangle([10, y + 10, 200, y + 45], fill=(60, 60, 90))
            draw.text((30, y + 18), "Clear All", fill=(180, 180, 200), font=sfont)

            desktop.state.notification_shade_open = False
        except Exception:
            pass

    return img


def render_quick_settings(desktop: DesktopBackend) -> Optional[object]:
    """Render quick settings panel pulled from top-right."""
    desktop.create_window("Terminal", 900, 600)
    desktop.state.quick_settings_open = True
    img = desktop.render_to_image()

    if img:
        try:
            from PIL import ImageDraw, ImageFont
            draw = ImageDraw.Draw(img)

            # Panel background (right side)
            panel_x = desktop._width - 400
            draw.rectangle([panel_x, 0, desktop._width, 400], fill=(25, 25, 40))

            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
                sfont = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
            except (OSError, IOError):
                font = ImageFont.load_default()
                sfont = font

            draw.text((panel_x + 20, 15), "Quick Settings", fill=(200, 200, 220), font=font)

            # Toggle buttons
            toggles = [
                ("📶  Wi-Fi", True, "Home-5G"),
                ("🔵  Bluetooth", True, "Connected"),
                ("🌙  Night Light", False, "Off"),
                ("✈️  Airplane", False, "Off"),
                ("📍  Location", True, "On"),
            ]

            y = 50
            for label, active, status in toggles:
                color = (40, 120, 200) if active else (50, 50, 70)
                draw.rounded_rectangle([panel_x + 15, y, panel_x + 185, y + 50],
                                       radius=8, fill=color)
                draw.text((panel_x + 25, y + 8), label, fill=(220, 220, 240), font=sfont)
                draw.text((panel_x + 25, y + 28), status, fill=(160, 160, 180), font=sfont)

                # Status dot
                dot_color = (80, 200, 120) if active else (100, 100, 120)
                draw.ellipse([panel_x + 165, y + 15, panel_x + 175, y + 25], fill=dot_color)
                y += 60

            # Sliders
            y += 10
            draw.text((panel_x + 20, y), "🔆 Brightness", fill=(160, 160, 180), font=sfont)
            y += 25
            draw.rectangle([panel_x + 15, y, panel_x + 350, y + 8], fill=(40, 40, 60))
            draw.rectangle([panel_x + 15, y, panel_x + 260, y + 8], fill=(100, 140, 200))

            y += 30
            draw.text((panel_x + 20, y), "🔊 Volume", fill=(160, 160, 180), font=sfont)
            y += 25
            draw.rectangle([panel_x + 15, y, panel_x + 350, y + 8], fill=(40, 40, 60))
            draw.rectangle([panel_x + 15, y, panel_x + 180, y + 8], fill=(100, 140, 200))

            # Battery
            y += 30
            draw.text((panel_x + 20, y), "🔋 Battery: 73%", fill=(160, 160, 180), font=sfont)

            desktop.state.quick_settings_open = False
        except Exception:
            pass

    return img


def render_settings(desktop: DesktopBackend) -> Optional[object]:
    """Render settings panel focused."""
    desktop.create_window("Terminal", 800, 500)
    settings = desktop.create_window("Settings — Appearance", 900, 650)
    desktop.focus_window(settings.id)
    return desktop.render_to_image()


def render_app_launcher(desktop: DesktopBackend) -> Optional[object]:
    """Render app launcher overlay."""
    desktop.create_window("Terminal", 900, 600)
    img = desktop.render_to_image()

    if img:
        try:
            from PIL import ImageDraw, ImageFont
            draw = ImageDraw.Draw(img)

            # Semi-transparent overlay
            overlay = Image.new("RGBA", img.size, (0, 0, 0, 180))
            img_rgba = img.convert("RGBA")
            img = Image.alpha_composite(img_rgba, overlay)
            draw = ImageDraw.Draw(img)

            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
                sfont = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
            except (OSError, IOError):
                font = ImageFont.load_default()
                sfont = font

            # Search bar
            cx, cy = desktop._width // 2, 100
            draw.rounded_rectangle([cx - 300, cy - 20, cx + 300, cy + 25],
                                   radius=12, fill=(40, 40, 60))
            draw.text((cx - 280, cy - 8), "🔍  Search apps...", fill=(120, 120, 140), font=sfont)

            # App grid
            apps = [
                ("💻", "Terminal"), ("📁", "Files"), ("⚙️", "Settings"),
                ("🌐", "Browser"), ("📝", "Editor"), ("🎵", "Music"),
                ("🖼️", "Photos"), ("📊", "Monitor"), ("🔧", "Utilities"),
                ("🎮", "Games"), ("📦", "Packages"), ("🔑", "Passwords"),
                ("📅", "Calendar"), ("📷", "Camera"), ("🗺️", "Maps"),
                ("🎨", "Color"), ("💾", "Backup"), ("🔒", "Security"),
            ]

            cols = 6
            start_x = cx - 330
            start_y = 180
            cell_w = 110
            cell_h = 100

            for i, (icon, name) in enumerate(apps):
                col = i % cols
                row = i // cols
                ax = start_x + col * cell_w
                ay = start_y + row * cell_h

                # App tile
                draw.rounded_rectangle([ax, ay, ax + 95, ay + 85],
                                       radius=8, fill=(40, 40, 60))
                draw.text((ax + 30, ay + 10), icon, fill=(200, 200, 220), font=font)
                draw.text((ax + 10, ay + 55), name, fill=(160, 160, 180), font=sfont)

        except Exception:
            pass

    return img


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

STATES = {
    "default": ("Default desktop with windows", render_default_desktop),
    "terminal": ("Terminal focused", render_terminal_focused),
    "file_manager": ("File manager focused", render_file_manager),
    "notifications": ("Notification shade (pull from top-left)", render_notification_shade),
    "quick_settings": ("Quick settings (pull from top-right)", render_quick_settings),
    "settings": ("Settings panel focused", render_settings),
    "app_launcher": ("App launcher grid", render_app_launcher),
}


def main():
    parser = argparse.ArgumentParser(
        description="Nyrqis OS — Live Desktop Demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--state", default=None, choices=list(STATES.keys()),
                        help="Render a specific state (default: all)")
    parser.add_argument("--backend", default=None,
                        choices=["linux", "nyrqis", "headless"],
                        help="Force a backend")
    parser.add_argument("--output", default="/tmp/nyrqis_demo",
                        help="Output directory for PNGs")
    parser.add_argument("--width", type=int, default=1440, help="Desktop width")
    parser.add_argument("--height", type=int, default=900, help="Desktop height")
    parser.add_argument("--live", action="store_true",
                        help="Interactive mode (requires display)")
    args = parser.parse_args()

    # Resolve backend
    backend_type = None
    if args.backend:
        backend_type = BackendType(args.backend)

    # Find shell design
    design = None
    candidates = [
        os.path.join(_HERE, "tests/fixtures/nstudio/desktop.nstudio"),
        os.path.expanduser("~/.nyrqis/shell.nstudio"),
    ]
    for path in candidates:
        if os.path.exists(path):
            design = path
            break

    print("╔══════════════════════════════════════════╗")
    print("║       Nyrqis OS — Desktop Demo           ║")
    print("╚══════════════════════════════════════════╝")
    print()

    if args.live:
        print("Live mode not yet implemented (needs display server)")
        print("Use --state to render individual screenshots")
        return

    # Create output directory
    os.makedirs(args.output, exist_ok=True)

    # Determine which states to render
    states_to_render = [args.state] if args.state else list(STATES.keys())

    for state_name in states_to_render:
        desc, render_fn = STATES[state_name]
        print(f"  Rendering: {state_name} — {desc}")

        desktop = create_desktop(
            design_path=design,
            backend_type=backend_type,
            width=args.width,
            height=args.height,
        )

        img = render_fn(desktop)
        if img is not None and hasattr(img, 'save'):
            path = os.path.join(args.output, f"{state_name}.png")
            img.save(path)
            print(f"    ✓ Saved: {path} ({img.size[0]}×{img.size[1]})")
        else:
            print(f"    ✗ Failed to render {state_name}")

    print()
    print(f"  Backend: {desktop.backend_type.value}")
    print(f"  Desktop: {args.width}×{args.height}")
    print(f"  Output:  {args.output}/")
    print(f"  States:  {len(states_to_render)}")
    print()
    print("  Done! 🍄")


if __name__ == "__main__":
    sys.exit(main() or 0)
