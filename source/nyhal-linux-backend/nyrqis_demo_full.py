#!/usr/bin/env python3
"""
Nyrqis OS — Complete Demo Script

Runs the full Nyrqis OS pipeline end-to-end:
1. Backend detection and initialization
2. Rust compositor startup (when available)
3. Boot animation rendering
4. Desktop rendering with windows
5. Notification shade, quick settings, app launcher
6. Screenshot capture
7. Animated GIF generation

Usage:
    python3 nyrqis_demo_full.py                  # full demo
    python3 nyrqis_demo_full.py --output /tmp/demo  # custom output
    python3 nyrqis_demo_full.py --backend nyrqis    # force backend
"""

import argparse
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def print_header(text: str):
    width = 60
    print(f"\n{'=' * width}")
    print(f"  {text}")
    print(f"{'=' * width}")


def print_step(num: int, total: int, text: str):
    print(f"\n  [{num}/{total}] {text}")


def run_demo(output_dir: str, backend: str = "auto"):
    """Run the complete Nyrqis demo."""
    total_steps = 8

    print_header("Nyrqis OS — Complete Demo")
    os.makedirs(output_dir, exist_ok=True)

    # Step 1: Backend detection
    print_step(1, total_steps, "Detecting backend...")
    from ui.backend_abstraction import get_backend, BackendType
    if backend == "nyrqis":
        bt = BackendType.NYRQIS
    elif backend == "linux":
        bt = BackendType.LINUX
    else:
        bt = BackendType.HEADLESS
    backend_obj = get_backend(bt)
    print(f"    Backend: {bt.value}")
    print(f"    Monitors: {len(backend_obj.display.enumerate_monitors())}")

    # Step 2: GPU initialization
    print_step(2, total_steps, "Initializing GPU...")
    backend_obj.gpu.initialize()
    print(f"    GPU initialized: True")

    # Step 3: Compositor startup
    print_step(3, total_steps, "Starting compositor...")
    from ui.rust_ffi import get_rust_backend
    rb = get_rust_backend()
    rust_available = rb.compositor.available
    if rust_available:
        rb.compositor.start()
        rb.compositor.add_output(1920, 1080, "default")
        print(f"    Rust compositor: active (ABI 0x{rb.compositor.version():08x})")
    else:
        print(f"    Rust compositor: unavailable (using PIL fallback)")

    # Step 4: Boot animation
    print_step(4, total_steps, "Rendering boot animation...")
    from nyrqis_boot_full import render_boot_sequence
    boot_dir = os.path.join(output_dir, "boot")
    render_boot_sequence(backend, num_frames=30, output_dir=boot_dir)
    print(f"    Boot animation: {boot_dir}/boot_animation.gif")

    # Step 5: Desktop rendering
    print_step(5, total_steps, "Rendering desktop...")
    from ui.desktop_preview import DesktopPreview
    preview = DesktopPreview(1920, 1080)
    preview.start()

    # Render all states
    states = [
        ("boot_splash", preview.render_boot_splash),
        ("default", preview.render_default_state),
        ("notifications", preview.render_notification_shade),
        ("quick_settings", preview.render_quick_settings),
        ("app_launcher", preview.render_app_launcher),
    ]

    for name, render_func in states:
        render_func()
        path = os.path.join(output_dir, f"{name}.png")
        preview.capture(path)
        print(f"    {name}: {path}")

    # Step 6: Screenshot
    print_step(6, total_steps, "Capturing screenshot...")
    preview.render_default_state()
    screenshot_path = os.path.join(output_dir, "desktop_screenshot.png")
    preview.capture(screenshot_path)
    from PIL import Image
    img = Image.open(screenshot_path)
    print(f"    Screenshot: {screenshot_path} ({img.size[0]}x{img.size[1]})")

    # Step 7: Animated GIF
    print_step(7, total_steps, "Generating animated GIF...")
    gif_path = os.path.join(output_dir, "desktop_animation.gif")
    preview.start_recording(gif_path, fps=10)
    for _ in range(10):
        preview.render_default_state()
        time.sleep(0.05)
    for _ in range(10):
        preview.render_notification_shade()
        time.sleep(0.05)
    for _ in range(10):
        preview.render_quick_settings()
        time.sleep(0.05)
    for _ in range(10):
        preview.render_app_launcher()
        time.sleep(0.05)
    preview.stop_recording()
    gif_size = os.path.getsize(gif_path) if os.path.exists(gif_path) else 0
    print(f"    GIF: {gif_path} ({gif_size // 1024}KB)")

    preview.stop()

    # Step 8: Wayland session demo
    print_step(8, total_steps, "Testing Wayland session...")
    from ui.wayland_session import WaylandSession
    session = WaylandSession(1280, 720)
    session.start()
    session.render_frame()
    session_screenshot = os.path.join(output_dir, "wayland_session.png")
    session.screenshot(session_screenshot)
    print(f"    Session screenshot: {session_screenshot}")
    session.stop()

    # Cleanup Rust
    if rust_available and rb.compositor.started:
        rb.compositor.stop()

    # Summary
    print_header("Demo Complete!")
    print(f"\n  Output directory: {output_dir}")
    print(f"  Files generated:")
    for f in sorted(os.listdir(output_dir)):
        if os.path.isfile(os.path.join(output_dir, f)):
            size = os.path.getsize(os.path.join(output_dir, f))
            print(f"    {f} ({size // 1024}KB)")
    print(f"\n  🍄 Nyrqis OS demo complete!")
    print()


def main():
    parser = argparse.ArgumentParser(description="Nyrqis OS Complete Demo")
    parser.add_argument("--output", default="/tmp/nyrqis_demo_full",
                        help="Output directory")
    parser.add_argument("--backend", choices=["nyrqis", "linux", "headless"],
                        default="headless", help="Backend type")
    args = parser.parse_args()
    run_demo(args.output, args.backend)


if __name__ == "__main__":
    main()
