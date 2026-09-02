#!/usr/bin/env python3
"""Nyrqis OS Boot Sequence Demo.

Renders the complete boot process as a series of PNG screenshots:
1. Kernel messages (BIOS/UEFI handoff, device detection)
2. Init script (daemon start, shell loading)
3. Desktop rendering (taskbar, windows, terminal)
4. Interactive terminal session

Usage:
    python3 demo/boot_sequence.py --output /tmp/nyrqis-boot

Each frame is a 1920x1080 PNG showing a phase of the boot process.
"""

from __future__ import annotations

import os
import sys
import time
from typing import List, Tuple

# Add parent directory to path
_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_DIR)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from ui.terminal import TerminalEmulator, TerminalConfig, AnsiColor, DEFAULT_PALETTE


# ---------------------------------------------------------------------------
# Simple PNG writer (no PIL dependency)
# ---------------------------------------------------------------------------

def _write_png(path: str, width: int, height: int, rgb_data: bytes) -> None:
    """Write an RGB image as PNG using zlib compression."""
    import zlib
    import struct
    
    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
    
    # IHDR
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    
    # IDAT - filter each row with None filter (0)
    raw = b""
    stride = width * 3
    for y in range(height):
        raw += b"\x00"  # None filter
        raw += rgb_data[y * stride : (y + 1) * stride]
    compressed = zlib.compress(raw, 9)
    
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_chunk(b"IHDR", ihdr))
        f.write(_chunk(b"IDAT", compressed))
        f.write(_chunk(b"IEND", b""))


# ---------------------------------------------------------------------------
# Boot sequence renderer
# ---------------------------------------------------------------------------

class BootSequenceRenderer:
    """Renders the Nyrqis OS boot sequence as PNG frames."""
    
    def __init__(self, width: int = 1920, height: int = 1080):
        self.width = width
        self.height = height
    
    def render_all(self, output_dir: str) -> List[str]:
        """Render all boot sequence frames.
        
        Returns list of output file paths.
        """
        os.makedirs(output_dir, exist_ok=True)
        frames = []
        
        # Phase 1: Kernel boot messages
        print("Phase 1: Rendering kernel boot messages...")
        path = os.path.join(output_dir, "01_kernel_boot.png")
        self._render_kernel_boot(path)
        frames.append(path)
        
        # Phase 2: Init script
        print("Phase 2: Rendering init script...")
        path = os.path.join(output_dir, "02_init_script.png")
        self._render_init_script(path)
        frames.append(path)
        
        # Phase 3: Shell loading
        print("Phase 3: Rendering shell loading...")
        path = os.path.join(output_dir, "03_shell_loading.png")
        self._render_shell_loading(path)
        frames.append(path)
        
        # Phase 4: Desktop with terminal
        print("Phase 4: Rendering desktop with terminal...")
        path = os.path.join(output_dir, "04_desktop_terminal.png")
        self._render_desktop_terminal(path)
        frames.append(path)
        
        # Phase 5: Interactive terminal session
        print("Phase 5: Rendering interactive terminal...")
        path = os.path.join(output_dir, "05_interactive_terminal.png")
        self._render_interactive_terminal(path)
        frames.append(path)
        
        return frames
    
    def _render_kernel_boot(self, output_path: str) -> None:
        """Render kernel boot messages phase."""
        term = TerminalEmulator(TerminalConfig(
            cols=80, rows=24, font_size=2, padding=16,
            bg_color=(0, 0, 0), fg_color=AnsiColor.WHITE,
        ))
        
        # Kernel messages
        messages = [
            ("[    0.000000] Linux version 6.8.0-nyrqis (gcc 13.2.0)", AnsiColor.WHITE),
            ("[    0.000000] Command line: BOOT_IMAGE=/vmlinuz nyroot=/dev/sda2", AnsiColor.WHITE),
            ("[    0.000000] BIOS-provided physical RAM map:", AnsiColor.WHITE),
            ("[    0.000000] BIOS-e820: [mem 0x0000000000000000-0x000000000009fbff] usable", AnsiColor.BRIGHT_BLACK),
            ("[    0.123456] tsc: Fast TSC calibration using PIT", AnsiColor.BRIGHT_BLACK),
            ("[    0.234567] Calibrating delay loop... 4789.23 BogoMIPS (lpj=2394616)", AnsiColor.WHITE),
            ("[    0.345678] CPU: 4x Intel Core i7-4770 @ 3.40GHz", AnsiColor.WHITE),
            ("[    0.456789] Memory: 16384MB available", AnsiColor.GREEN),
            ("", AnsiColor.WHITE),
            ("[    1.000000] Nyrqis OS booting...", AnsiColor.CYAN),
            ("[    1.100000] Loading kernel modules...", AnsiColor.WHITE),
            ("[    1.200000] DRM driver loaded: i915", AnsiColor.GREEN),
            ("[    1.300000] GPU: Intel HD Graphics 4600", AnsiColor.GREEN),
            ("[    1.400000] Input: evdev registered", AnsiColor.WHITE),
            ("[    1.500000] USB: xHCI controller found", AnsiColor.WHITE),
            ("[    1.600000] NVMe: controller detected", AnsiColor.WHITE),
            ("[    1.700000] Filesystem: mounting root...", AnsiColor.WHITE),
            ("[    1.800000] EXT4-fs (sda2): mounted filesystem", AnsiColor.GREEN),
            ("", AnsiColor.WHITE),
            ("[    2.000000] Starting Nyrqis init...", AnsiColor.CYAN),
            ("[    2.100000] Device nodes: created /dev/dri/card0", AnsiColor.GREEN),
            ("[    2.200000] GPU buffers: GBM initialized", AnsiColor.GREEN),
            ("[    2.300000] Wayland: compositor socket ready", AnsiColor.GREEN),
            ("", AnsiColor.WHITE),
            ("[    2.500000] Nyrqis kernel boot complete!", AnsiColor.GREEN),
            ("", AnsiColor.WHITE),
            ("$ █", AnsiColor.WHITE),
        ]
        
        for text, color in messages:
            term.feed(f"\x1b[{color.value + 30}m{text}\x1b[0m\r\n")
        
        # Render
        pixels, w, h = term.render_pixels()
        rgb_data = b""
        for r, g, b in pixels:
            rgb_data += bytes([r, g, b])
        _write_png(output_path, w, h, rgb_data)
    
    def _render_init_script(self, output_path: str) -> None:
        """Render init script phase."""
        term = TerminalEmulator(TerminalConfig(
            cols=80, rows=24, font_size=2, padding=16,
            bg_color=(0, 0, 0), fg_color=AnsiColor.WHITE,
        ))
        
        # Init messages
        lines = [
            "\x1b[1;36m╔══════════════════════════════════════════════╗\x1b[0m",
            "\x1b[1;36m║           \x1b[1;37mNyrqis Init v0.25.0\x1b[0;36m                  ║\x1b[0m",
            "\x1b[1;36m╚══════════════════════════════════════════════╝\x1b[0m",
            "",
            "\x1b[1;33mPhase 1: Starting backend daemon...\x1b[0m",
            "  \x1b[32m✓\x1b[0m Loading configuration...",
            "  \x1b[32m✓\x1b[0m Starting IPC socket...",
            "  \x1b[32m✓\x1b[0m Backend daemon started (PID 1234)",
            "",
            "\x1b[1;33mPhase 2: Loading shell design...\x1b[0m",
            "  \x1b[32m✓\x1b[0m Parsing desktop.nstudio...",
            "  \x1b[32m✓\x1b[0m Validating components...",
            "  \x1b[32m✓\x1b[0m Shell design loaded (24 components)",
            "",
            "\x1b[1;33mPhase 3: Initializing GPU...\x1b[0m",
            "  \x1b[32m✓\x1b[0m DRM device: /dev/dri/card0",
            "  \x1b[32m✓\x1b[0m GBM surface created (1920x1080)",
            "  \x1b[32m✓\x1b[0m EGL context initialized",
            "  \x1b[32m✓\x1b[0m Vulkan instance created",
            "",
            "\x1b[1;33mPhase 4: Starting Wayland compositor...\x1b[0m",
            "  \x1b[32m✓\x1b[0m Compositor socket: /run/nyrqis/wayland-0",
            "  \x1b[32m✓\x1b[0m Protocol codec ready",
            "  \x1b[32m✓\x1b[0m Frame callbacks enabled",
            "",
            "\x1b[1;32mInit complete! Starting desktop session...\x1b[0m",
            "",
            "$ █",
        ]
        
        for line in lines:
            term.feed(f"{line}\r\n")
        
        # Render
        pixels, w, h = term.render_pixels()
        rgb_data = b""
        for r, g, b in pixels:
            rgb_data += bytes([r, g, b])
        _write_png(output_path, w, h, rgb_data)
    
    def _render_shell_loading(self, output_path: str) -> None:
        """Render shell loading phase."""
        term = TerminalEmulator(TerminalConfig(
            cols=80, rows=24, font_size=2, padding=16,
            bg_color=(0, 0, 0), fg_color=AnsiColor.WHITE,
        ))
        
        lines = [
            "\x1b[1;36m╔══════════════════════════════════════════════╗\x1b[0m",
            "\x1b[1;36m║         \x1b[1;37mNyrqis Desktop Shell\x1b[0;36m                  ║\x1b[0m",
            "\x1b[1;36m╚══════════════════════════════════════════════╝\x1b[0m",
            "",
            "\x1b[1;33mLoading components...\x1b[0m",
            "  \x1b[32m✓\x1b[0m DesktopSurface   \x1b[90m(1920x1080)\x1b[0m",
            "  \x1b[32m✓\x1b[0m Taskbar          \x1b[90m(bottom, 48px)\x1b[0m",
            "  \x1b[32m✓\x1b[0m StartMenu        \x1b[90m(5 items)\x1b[0m",
            "  \x1b[32m✓\x1b[0m NotificationCenter\x1b[90m(enabled)\x1b[0m",
            "  \x1b[32m✓\x1b[0m QuickSettings    \x1b[90m(wifi, bluetooth, volume)\x1b[0m",
            "",
            "\x1b[1;33mLoading themes...\x1b[0m",
            "  \x1b[32m✓\x1b[0m Eclipse (default)",
            "  \x1b[32m✓\x1b[0m Solar",
            "",
            "\x1b[1;33mApplying bindings...\x1b[0m",
            "  \x1b[32m✓\x1b[0m State: theme=Eclipse",
            "  \x1b[32m✓\x1b[0m State: volume=75",
            "  \x1b[32m✓\x1b[0m State: brightness=100",
            "",
            "\x1b[1;32mDesktop ready! Rendering...\x1b[0m",
            "",
            "$ █",
        ]
        
        for line in lines:
            term.feed(f"{line}\r\n")
        
        # Render
        pixels, w, h = term.render_pixels()
        rgb_data = b""
        for r, g, b in pixels:
            rgb_data += bytes([r, g, b])
        _write_png(output_path, w, h, rgb_data)
    
    def _render_desktop_terminal(self, output_path: str) -> None:
        """Render desktop with terminal window."""
        # Create a terminal with typical desktop session output
        term = TerminalEmulator(TerminalConfig(
            cols=80, rows=20, font_size=1, padding=4,
            bg_color=(20, 20, 28), fg_color=AnsiColor.WHITE,
        ))
        
        # Simulate a terminal session
        session_lines = [
            "\x1b[1;32m╔══════════════════════════════════════════════════════════════════════════════╗\x1b[0m",
            "\x1b[1;32m║                          \x1b[1;37mNyrqis OS Terminal\x1b[0;32m                                ║\x1b[0m",
            "\x1b[1;32m╚══════════════════════════════════════════════════════════════════════════════╝\x1b[0m",
            "",
            "\x1b[1;37mnyrqis\x1b[0;36m@\x1b[0;37mdesktop\x1b[0;37m:\x1b[0;34m~\x1b[0;37m$ \x1b[0mnyrqisctl status",
            "  \x1b[32m●\x1b[0m Backend:   running (PID 1234)",
            "  \x1b[32m●\x1b[0m Session:   active",
            "  \x1b[32m●\x1b[0m Output:    1920x1080@60Hz",
            "  \x1b[32m●\x1b[0m GPU:       Intel HD Graphics (Mesa 23.2)",
            "  \x1b[32m●\x1b[0m Compositor: Wayland (active)",
            "",
            "\x1b[1;37mnyrqis\x1b[0;36m@\x1b[0;37mdesktop\x1b[0;37m:\x1b[0;34m~\x1b[0;37m$ \x1b[0mnyrqis-ctl app list",
            "  Terminal        v1.0.0    \x1b[32minstalled\x1b[0m",
            "  Files           v1.2.0    \x1b[32minstalled\x1b[0m",
            "  Browser         v2.1.0    \x1b[32minstalled\x1b[0m",
            "  Settings        v1.0.0    \x1b[32minstalled\x1b[0m",
            "",
            "\x1b[1;37mnyrqis\x1b[0;36m@\x1b[0;37mdesktop\x1b[0;37m:\x1b[0;34m~\x1b[0;37m$ \x1b[0mnyrqis-init --diagnose",
            "\x1b[1;36mSystem Diagnostics:\x1b[0m",
            "  \x1b[32m✓\x1b[0m DRM device:    /dev/dri/card0",
            "  \x1b[32m✓\x1b[0m GBM:           available",
            "  \x1b[32m✓\x1b[0m EGL:           available",
            "  \x1b[32m✓\x1b[0m Vulkan:        available",
            "  \x1b[32m✓\x1b[0m Wayland:       ready",
            "  \x1b[32m✓\x1b[0m Shell:         loaded",
            "  \x1b[32m✓\x1b[0m All checks passed!",
            "",
            "\x1b[1;37mnyrqis\x1b[0;36m@\x1b[0;37mdesktop\x1b[0;37m:\x1b[0;34m~\x1b[0;37m$ \x1b[0m█",
        ]
        
        for line in session_lines:
            term.feed(f"{line}\r\n")
        
        # Now composite the terminal into a desktop scene
        self._render_composite(output_path, term)
    
    def _render_interactive_terminal(self, output_path: str) -> None:
        """Render interactive terminal with commands being typed."""
        term = TerminalEmulator(TerminalConfig(
            cols=80, rows=20, font_size=1, padding=4,
            bg_color=(20, 20, 28), fg_color=AnsiColor.WHITE,
        ))
        
        session = [
            "\x1b[1;37mnyrqis\x1b[0;36m@\x1b[0;37mdesktop\x1b[0;37m:\x1b[0;34m~\x1b[0;37m$ \x1b[0muname -a",
            "Linux nydesktop 6.8.0-nyrqis #1 SMP PREEMPT_DYNAMIC x86_64 GNU/Nyrqis",
            "",
            "\x1b[1;37mnyrqis\x1b[0;36m@\x1b[0;37mdesktop\x1b[0;37m:\x1b[0;34m~\x1b[0;37m$ \x1b[0mls -la /dev/dri/",
            "total 0",
            "drwxr-xr-x  3 root root     100 Sep  2 14:00 .",
            "drwxr-xr-x 20 root root    4096 Sep  2 14:00 ..",
            "drwxr-xr-x  2 root root      60 Sep  2 14:00 by-path",
            "crw-rw----  1 root video 226,   0 Sep  2 14:00 card0",
            "crw-rw----  1 root video 226, 128 Sep  2 14:00 renderD128",
            "",
            "\x1b[1;37mnyrqis\x1b[0;36m@\x1b[0;37mdesktop\x1b[0;37m:\x1b[0;34m~\x1b[0;37m$ \x1b[0mglxinfo | head -5",
            "name of display: :0",
            "display: :0  screen: 0",
            "direct rendering: Yes",
            "Extended renderer info (GLX_MESA_query_renderer):",
            "    Vendor: Intel Open Source Technology Center",
            "",
            "\x1b[1;37mnyrqis\x1b[0;36m@\x1b[0;37mdesktop\x1b[0;37m:\x1b[0;34m~\x1b[0;37m$ \x1b[0mvulkaninfo --summary",
            "========== VULKAN INFO ==========",
            "",
            "GPU id       : 0 (Intel(R) HD Graphics 4600)",
            "API version  : 1.3.250",
            "Driver version: 23.2.1",
            "",
            "\x1b[1;37mnyrqis\x1b[0;36m@\x1b[0;37mdesktop\x1b[0;37m:\x1b[0;34m~\x1b[0;37m$ \x1b[0m█",
        ]
        
        for line in session:
            term.feed(f"{line}\r\n")
        
        self._render_composite(output_path, term)
    
    def _render_composite(self, output_path: str, term: TerminalEmulator) -> None:
        """Render a desktop composite with the terminal embedded.
        
        Uses the terminal's own pixel renderer for speed, then composites
        into a 1920x1080 desktop scene.
        """
        # Colors
        BG_TOP = (24, 24, 32)
        BG_BOTTOM = (40, 40, 56)
        TASKBAR = (32, 32, 44)
        WINDOW_BG = (35, 35, 48)
        TITLE_BG = (50, 50, 68)
        ACCENT = (80, 140, 255)
        BORDER = (60, 60, 80)
        TEXT_DIM = (120, 120, 140)
        TEXT_WHITE = (200, 200, 220)
        
        width = self.width
        height = self.height
        
        # Render terminal at its native size first (fast)
        term_pixels, tw, th = term.render_pixels()
        
        # Window dimensions - fit terminal content
        win_x, win_y = 160, 40
        win_w = max(tw + 20, 800)
        win_h = max(th + 60, 500)
        
        # Ensure window fits on screen
        if win_x + win_w > width:
            win_w = width - win_x - 20
        if win_y + win_h > height - 50:
            win_h = height - win_y - 60
        
        # Build the image
        pixels = [BG_TOP] * (width * height)
        
        def set_pixel(px: int, py: int, color: Tuple[int, int, int]) -> None:
            if 0 <= px < width and 0 <= py < height:
                pixels[py * width + px] = color
        
        def fill_rect(rx: int, ry: int, rw: int, rh: int, color: Tuple[int, int, int]) -> None:
            for dy in range(rh):
                for dx in range(rw):
                    set_pixel(rx + dx, ry + dy, color)
        
        # Background gradient (only the rows outside the window)
        for y in range(height):
            t = y / height
            r = int(BG_TOP[0] * (1 - t) + BG_BOTTOM[0] * t)
            g = int(BG_TOP[1] * (1 - t) + BG_BOTTOM[1] * t)
            b = int(BG_TOP[2] * (1 - t) + BG_BOTTOM[2] * t)
            for x in range(width):
                pixels[y * width + x] = (r, g, b)
        
        # Taskbar
        fill_rect(0, height - 48, width, 48, TASKBAR)
        fill_rect(0, height - 48, width, 1, BORDER)
        
        # Start button
        fill_rect(8, height - 40, 32, 32, ACCENT)
        
        # Clock area
        fill_rect(width - 120, height - 40, 80, 32, (50, 50, 68))
        
        # Window shadow
        fill_rect(win_x + 4, win_y + 4, win_w, win_h, (10, 10, 15))
        
        # Window border
        fill_rect(win_x, win_y, win_w, win_h, ACCENT)
        
        # Window background
        fill_rect(win_x + 1, win_y + 1, win_w - 2, win_h - 2, WINDOW_BG)
        
        # Title bar
        title_h = 32
        fill_rect(win_x + 1, win_y + 1, win_w - 2, title_h, TITLE_BG)
        
        # Close button
        fill_rect(win_x + win_w - 28, win_y + 10, 14, 14, (220, 60, 60))
        # Minimize button
        fill_rect(win_x + win_w - 48, win_y + 10, 14, 14, (60, 180, 120))
        # Maximize button
        fill_rect(win_x + win_w - 68, win_y + 10, 14, 14, (60, 140, 220))
        
        # Title text (simple block)
        fill_rect(win_x + 12, win_y + 10, 120, 14, TEXT_WHITE)
        
        # Composite terminal pixels into the window
        term_x = win_x + 10
        term_y = win_y + title_h + 4
        
        for ty in range(min(th, win_h - title_h - 8)):
            for tx in range(min(tw, win_w - 20)):
                idx = ty * tw + tx
                if idx < len(term_pixels):
                    color = term_pixels[idx]
                    # Skip black (background) pixels
                    if color != term.config.bg_color:
                        set_pixel(term_x + tx, term_y + ty, color)
        
        # Render to RGB bytes
        rgb_data = bytearray(width * height * 3)
        i = 0
        for r, g, b in pixels:
            rgb_data[i] = r
            rgb_data[i+1] = g
            rgb_data[i+2] = b
            i += 3
        _write_png(output_path, width, height, bytes(rgb_data))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    """Render the boot sequence demo."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Nyrqis OS Boot Sequence Demo")
    parser.add_argument("--output", "-o", default="/tmp/nyrqis-boot",
                       help="Output directory for PNG frames")
    parser.add_argument("--width", type=int, default=1920,
                       help="Render width")
    parser.add_argument("--height", type=int, default=1080,
                       help="Render height")
    args = parser.parse_args()
    
    print("╔══════════════════════════════════════════╗")
    print("║        Nyrqis Boot Sequence Demo         ║")
    print("╚══════════════════════════════════════════╝")
    print()
    print(f"Resolution: {args.width}x{args.height}")
    print(f"Output: {args.output}")
    print()
    
    renderer = BootSequenceRenderer(args.width, args.height)
    frames = renderer.render_all(args.output)
    
    print()
    print("════════════════════════════════════════════")
    print("  Boot sequence demo complete!")
    print(f"  {len(frames)} frames saved to: {args.output}")
    print()
    for i, f in enumerate(frames, 1):
        size = os.path.getsize(f)
        print(f"  {i}. {os.path.basename(f)} ({size // 1024} KB)")
    print("════════════════════════════════════════════")


if __name__ == "__main__":
    main()
