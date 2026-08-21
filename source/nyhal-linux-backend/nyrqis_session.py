#!/usr/bin/env python3
"""nyrqis_session — interactive desktop session launcher.

Opens a .nstudio design in a live SDL2 window with full keyboard and
mouse input.  This is the Nyrqis counterpart of a desktop environment:
it reads a design from Nyforge and presents it as an interactive shell.

Usage::

    python3 nyrqis_session.py shell.nstudio
    python3 nyrqis_session.py shell.nstudio --width 1920 --height 1080
    python3 nyrqis_session.py shell.nstudio --theme Solar
    python3 nyrqis_session.py shell.nstudio --screenshot  # headless, one frame

Architecture:
    SDL2 event loop → DesktopSession.process_* → live_render → SDL2 present

References:
    - ADR-0025 §9: runtime consumption
    - doc #14: Nyrqis Desktop Shell as a running product
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from typing import Optional

# Ensure the backend is on the path
_BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from ui.desktop_session import (
    DesktopSession,
    EventType,
    KeyEvent,
    MouseButton,
    MouseEvent,
)
from ui.nstudio import load

logger = logging.getLogger("nyrqis.session")

# ---------------------------------------------------------------------------
# SDL2 key mapping
# ---------------------------------------------------------------------------

# SDL2 keycodes → logical key names
_SDL_KEY_MAP = {
    # Letters (SDLK_a .. SDLK_z)
    **{97 + i: chr(ord("a") + i) for i in range(26)},
    # Digits (SDLK_0 .. SDLK_9)
    **{48 + i: str(i) for i in range(10)},
    # Special keys
    13: "Return",
    27: "Escape",
    32: "Space",
    8: "Backspace",
    9: "Tab",
    127: "Delete",
    # Arrow keys
    1073741903: "Right",
    1073741904: "Left",
    1073741905: "Down",
    1073741906: "Up",
    # F keys
    **{1073741882 + i: f"F{i+1}" for i in range(12)},
}


def _sdl_mouse_button(sdl_button: int) -> MouseButton:
    """Convert SDL2 mouse button to MouseButton enum."""
    if sdl_button == 1:
        return MouseButton.LEFT
    elif sdl_button == 3:
        return MouseButton.RIGHT
    elif sdl_button == 2:
        return MouseButton.MIDDLE
    return MouseButton.NONE


def run_session(
    doc_path: str,
    width: Optional[int] = None,
    height: Optional[int] = None,
    theme: Optional[str] = None,
    headless: bool = False,
    screenshot_path: Optional[str] = None,
    fps: int = 60,
) -> None:
    """Run an interactive desktop session from a .nstudio file.

    Parameters
    ----------
    doc_path : str
        Path to the .nstudio file to open.
    width, height : int, optional
        Window dimensions.  Defaults to the document's screen size.
    theme : str, optional
        Override the document's theme.
    headless : bool
        If True, render one frame and exit (for testing).
    screenshot_path : str, optional
        Save a screenshot to this path on exit.
    fps : int
        Target frames per second.
    """
    # Load the document
    logger.info("Loading design: %s", doc_path)
    doc = load(doc_path)

    # Create the session
    session = DesktopSession(doc)
    logger.info(
        "Session: %d windows, %d components, %d behaviors",
        len(session.windows),
        len(doc.component_ids()),
        len(doc.behaviors),
    )

    # Determine window size
    screen = doc.screens[0] if doc.screens else None
    if screen:
        win_w = width or screen.size.get("width", 1920)
        win_h = height or screen.size.get("height", 1080)
    else:
        win_w = width or 1920
        win_h = height or 1080

    if theme:
        doc.themes["active"] = theme

    # --- Headless mode (no SDL2) ---
    if headless:
        logger.info("Headless mode — rendering one frame")
        img = session.live_render()
        out = screenshot_path or "/tmp/nyrqis_session.png"
        img.save(out)
        logger.info("Saved: %s (%dx%d)", out, img.size[0], img.size[1])
        print(f"Rendered {out} ({img.size[0]}x{img.size[1]})")
        return

    # --- Interactive SDL2 mode ---
    try:
        import sdl2
        import sdl2.ext
    except ImportError:
        logger.error("pysdl2 not installed — falling back to headless")
        run_session(
            doc_path, width, height, theme,
            headless=True, screenshot_path=screenshot_path)
        return

    # Initialize SDL2
    sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO)

    window = sdl2.SDL_CreateWindow(
        b"Nyrqis Desktop Shell",
        sdl2.SDL_WINDOWPOS_CENTERED,
        sdl2.SDL_WINDOWPOS_CENTERED,
        win_w, win_h,
        sdl2.SDL_WINDOW_SHOWN | sdl2.SDL_WINDOW_RESIZABLE,
    )
    if not window:
        logger.error("Failed to create window: %s", sdl2.SDL_GetError())
        return

    renderer = sdl2.SDL_CreateRenderer(
        window, -1,
        sdl2.SDL_RENDERER_ACCELERATED | sdl2.SDL_RENDERER_PRESENTVSYNC,
    )
    if not renderer:
        # Fallback to software renderer
        renderer = sdl2.SDL_CreateRenderer(window, -1, 0)

    logger.info("Window: %dx%d @ %d fps", win_w, win_h, fps)

    # State
    running = True
    frame_time = 1.0 / fps
    frame_count = 0
    last_time = time.monotonic()
    needs_render = True

    # Register focus change callback to trigger re-render
    session.on_event(EventType.FOCUS_CHANGE, lambda e: setattr(
        run_session, "_needs_render", True))

    def _on_any_event(_event):
        nonlocal needs_render
        needs_render = True

    for et in (EventType.MOUSE_DOWN, EventType.MOUSE_UP,
               EventType.MOUSE_MOVE, EventType.KEY_DOWN,
               EventType.WINDOW_CLOSE):
        session.on_event(et, _on_any_event)

    # Main event loop
    try:
        while running:
            frame_start = time.monotonic()

            # Process SDL2 events
            event = sdl2.SDL_Event()
            while sdl2.SDL_PollEvent(event):
                etype = event.type

                if etype == sdl2.SDL_QUIT:
                    running = False
                    break

                elif etype == sdl2.SDL_KEYDOWN:
                    key = event.key.keysym.sym
                    mods = event.key.keysym.mod
                    ctrl = bool(mods & sdl2.KMOD_CTRL)
                    shift = bool(mods & sdl2.KMOD_SHIFT)
                    alt = bool(mods & sdl2.KMOD_ALT)
                    super_key = bool(mods & sdl2.KMOD_GUI)
                    key_name = _SDL_KEY_MAP.get(key, f"unknown_{key}")
                    session.process_key_event(KeyEvent(
                        key=key_name, ctrl=ctrl, shift=shift,
                        alt=alt, super_key=super_key))
                    needs_render = True

                elif etype == sdl2.SDL_MOUSEBUTTONDOWN:
                    mx, my = event.button.x, event.button.y
                    button = _sdl_mouse_button(event.button.button)
                    session.process_mouse_event(MouseEvent(
                        x=mx, y=my, button=button))
                    needs_render = True

                elif etype == sdl2.SDL_MOUSEBUTTONUP:
                    mx, my = event.button.x, event.button.y
                    button = _sdl_mouse_button(event.button.button)
                    session.process_mouse_up(MouseEvent(
                        x=mx, y=my, button=button))
                    needs_render = True

                elif etype == sdl2.SDL_MOUSEMOTION:
                    mx, my = event.motion.x, event.motion.y
                    session.process_mouse_event(MouseEvent(
                        x=mx, y=my, button=MouseButton.NONE))
                    needs_render = True

                elif etype == sdl2.SDL_WINDOWEVENT:
                    if event.window.event == sdl2.SDL_WINDOWEVENT_RESIZED:
                        needs_render = True

            # Render
            if needs_render:
                img = session.live_render()
                _blit_pil_to_renderer(renderer, img)
                sdl2.SDL_RenderPresent(renderer)
                needs_render = False
                frame_count += 1

            # Frame timing
            elapsed = time.monotonic() - frame_start
            sleep_time = frame_time - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        logger.info("Interrupted")
    finally:
        # Save screenshot on exit
        if screenshot_path:
            img = session.live_render()
            img.save(screenshot_path)
            logger.info("Screenshot saved: %s", screenshot_path)

        sdl2.SDL_DestroyRenderer(renderer)
        sdl2.SDL_DestroyWindow(window)
        sdl2.SDL_Quit()

    logger.info(
        "Session ended: %d frames, %d events",
        frame_count, len(session.event_log))


def _blit_pil_to_renderer(renderer, img) -> None:
    """Blit a PIL Image to an SDL2 renderer."""
    try:
        import ctypes
        # Convert PIL image to raw bytes
        raw = img.tobytes()
        w, h = img.size

        # Create an SDL texture from the raw RGB data
        texture = sdl2.SDL_CreateTexture(
            renderer,
            sdl2.SDL_PIXELFORMAT_RGB24,
            sdl2.SDL_TEXTUREACCESS_STREAMING,
            w, h,
        )
        if texture:
            sdl2.SDL_UpdateTexture(
                texture, None, raw, w * 3)
            sdl2.SDL_RenderCopy(renderer, texture, None, None)
            sdl2.SDL_DestroyTexture(texture)
    except Exception as e:
        # Fallback: just clear and present
        logger.warning("Blit failed: %s", e)
        bg = session.theme if hasattr(session, 'theme') else (30, 30, 30)
        sdl2.SDL_SetRenderDrawColor(renderer, 30, 30, 30, 255)
        sdl2.SDL_RenderClear(renderer)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Nyrqis interactive desktop session",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 nyrqis_session.py shell.nstudio
  python3 nyrqis_session.py shell.nstudio --theme Solar
  python3 nyrqis_session.py shell.nstudio --headless --screenshot /tmp/out.png
        """,
    )
    parser.add_argument(
        "design", help="Path to .nstudio design file")
    parser.add_argument(
        "--width", type=int, default=None,
        help="Window width (default: from document)")
    parser.add_argument(
        "--height", type=int, default=None,
        help="Window height (default: from document)")
    parser.add_argument(
        "--theme", default=None,
        help="Theme override (Eclipse, Solar)")
    parser.add_argument(
        "--headless", action="store_true",
        help="Render one frame without a window (for CI/testing)")
    parser.add_argument(
        "--screenshot", default=None, nargs="?", const="/tmp/nyrqis_session.png",
        help="Save screenshot on exit")
    parser.add_argument(
        "--fps", type=int, default=60,
        help="Target frames per second (default: 60)")
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable verbose logging")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    if not os.path.exists(args.design):
        logger.error("Design file not found: %s", args.design)
        sys.exit(1)

    run_session(
        doc_path=args.design,
        width=args.width,
        height=args.height,
        theme=args.theme,
        headless=args.headless,
        screenshot_path=args.screenshot,
        fps=args.fps,
    )


if __name__ == "__main__":
    main()
