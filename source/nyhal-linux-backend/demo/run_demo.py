#!/usr/bin/env python3
"""run_demo — Complete Nyrqis live demo.

Renders a full Nyrqis desktop environment and saves screenshots:

1. Clean desktop with gradient background
2. Desktop with icons
3. Open windows (Terminal, Files, Browser)
4. Start menu open
5. Final composite

Usage:
    python3 demo/run_demo.py
    python3 demo/run_demo.py --output /tmp/nyrqis-demo
    python3 demo/run_demo.py --resolution 1280x720

References:
    - ADR-0026: Wayland display-server integration
    - shell/defaults/desktop.nstudio
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
import time

# Ensure the backend is importable
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def save_png(width: int, height: int, pixels: bytes, filepath: str):
    """Save raw ARGB pixels as a PNG file (minimal implementation)."""
    import zlib
    
    def make_chunk(chunk_type: bytes, data: bytes) -> bytes:
        chunk = chunk_type + data
        return struct.pack(">I", len(data)) + chunk + struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF)
    
    # PNG signature
    signature = b"\x89PNG\r\n\x1a\n"
    
    # IHDR chunk
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    ihdr = make_chunk(b"IHDR", ihdr_data)
    
    # IDAT chunk (image data)
    raw_data = bytearray()
    for y in range(height):
        raw_data.append(0)  # filter byte (none)
        for x in range(width):
            offset = (y * width + x) * 4
            raw_data.extend(pixels[offset:offset + 4])
    
    compressed = zlib.compress(bytes(raw_data), 9)
    idat = make_chunk(b"IDAT", compressed)
    
    # IEND chunk
    iend = make_chunk(b"IEND", b"")
    
    # Write PNG
    with open(filepath, "wb") as f:
        f.write(signature + ihdr + idat + iend)


def run_demo(output_dir: str, width: int, height: int):
    """Run the complete Nyrqis demo."""
    from demo.desktop_renderer import DesktopRenderer
    
    os.makedirs(output_dir, exist_ok=True)
    
    print("╔══════════════════════════════════════════╗")
    print("║        Nyrqis Live Demo                  ║")
    print("╚══════════════════════════════════════════╝")
    print()
    print(f"Resolution: {width}x{height}")
    print(f"Output: {output_dir}")
    print()
    
    # Phase 1: Clean desktop (background only)
    print("Phase 1: Rendering clean desktop...")
    renderer = DesktopRenderer(width, height)
    renderer._draw_gradient()
    renderer._draw_taskbar()
    save_png(width, height, renderer.get_pixels(), os.path.join(output_dir, "01_desktop.png"))
    print("  ✓ Saved 01_desktop.png")
    
    # Phase 2: Desktop with icons only
    print("Phase 2: Rendering desktop icons...")
    renderer = DesktopRenderer(width, height)
    renderer._draw_gradient()
    renderer._setup_icons()
    renderer._draw_icons()
    renderer._draw_taskbar()
    save_png(width, height, renderer.get_pixels(), os.path.join(output_dir, "02_icons.png"))
    print("  ✓ Saved 02_icons.png")
    
    # Phase 3: Desktop with windows
    print("Phase 3: Opening application windows...")
    renderer = DesktopRenderer(width, height)
    renderer.render()
    save_png(width, height, renderer.get_pixels(), os.path.join(output_dir, "03_windows.png"))
    print("  ✓ Saved 03_windows.png")
    
    # Phase 4: Start menu
    print("Phase 4: Opening start menu...")
    renderer = DesktopRenderer(width, height)
    renderer.toggle_menu()
    renderer.render()
    save_png(width, height, renderer.get_pixels(), os.path.join(output_dir, "04_menu.png"))
    print("  ✓ Saved 04_menu.png")
    
    # Phase 5: Full demo
    print("Phase 5: Rendering final composite...")
    renderer = DesktopRenderer(width, height)
    renderer.toggle_menu()
    renderer.render()
    save_png(width, height, renderer.get_pixels(), os.path.join(output_dir, "nyrqis_demo.png"))
    print("  ✓ Saved nyrqis_demo.png")
    
    print()
    print("════════════════════════════════════════════")
    print("  Demo complete!")
    print(f"  Screenshots saved to: {output_dir}")
    print("════════════════════════════════════════════")
    print()
    
    # Also verify the compositor can render
    print("Verifying compositor rendering pipeline...")
    try:
        from ui.render_pipeline import RenderPipeline, RenderConfig
        config = RenderConfig(width=width, height=height, use_gbm=False, use_egl=False)
        pipeline = RenderPipeline(config)
        stats = pipeline.get_stats()
        print(f"  ✓ Render pipeline ready (frame_count={stats['frame_count']})")
        pipeline.cleanup()
    except Exception as exc:
        print(f"  ⚠ Render pipeline: {exc}")
    
    # Verify GPU pipelines
    print("Verifying GPU pipelines...")
    try:
        from ui import gbm_codec, egl_codec, vulkan_codec
        print(f"  ✓ GBM: {'available' if gbm_codec.is_available() else 'stub'}")
        print(f"  ✓ EGL: {'available' if egl_codec.is_available() else 'stub'}")
        print(f"  ✓ Vulkan: {'available' if vulkan_codec.is_available() else 'stub'}")
    except ImportError:
        print("  ⚠ GPU codecs not available")
    
    print()
    print("Nyrqis is ready! 🍄")


def main():
    parser = argparse.ArgumentParser(
        prog="nyrqis-demo",
        description="Complete Nyrqis live demo — renders a full desktop environment",
    )
    parser.add_argument("--output", "-o", default="/tmp/nyrqis-demo",
                       help="Output directory for screenshots")
    parser.add_argument("--resolution", "-r", default="1920x1080",
                       help="Resolution (WxH)")
    
    args = parser.parse_args()
    
    # Parse resolution
    try:
        w, h = args.resolution.split("x")
        width, height = int(w), int(h)
    except ValueError:
        print(f"Invalid resolution: {args.resolution}")
        sys.exit(1)
    
    run_demo(args.output, width, height)


if __name__ == "__main__":
    main()
