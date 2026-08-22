#!/usr/bin/env python3
"""screenshot — Nyrqis desktop screenshot tool.

Captures the current desktop session state (via the compositor's
live_render) and saves it as a PNG/JPEG image.  Supports:

- Full-screen capture
- Region selection (rectangle)
- Delayed capture
- Clipboard integration (via PIL Image.copy)
- Multi-monitor capture

This is a floor-level implementation — on a real OS the screenshot
tool would read from DRM/KMS or the Wayland protocol.  On the floor
it delegates to the compositor's render path.

Usage::

    from ui.screenshot import ScreenCapture
    from ui.desktop_session import DesktopSession

    session = DesktopSession.from_file('shell.nstudio')
    capture = ScreenCapture(session)

    # Full screen
    img = capture.grab_fullscreen()
    capture.save(img, '/tmp/nyrqis_desktop.png')

    # Region
    img = capture.grab_region(100, 100, 800, 600)

    # Delayed
    img = capture.grab_fullscreen(delay=3.0)

References:
    - NFS-001 §9: compositor
    - doc #14: Nyrqis Desktop Shell
"""

from __future__ import annotations

import datetime
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    raise ImportError("PIL/Pillow is required: pip install Pillow")

logger = logging.getLogger(__name__)


@dataclass
class CaptureRegion:
    """A rectangular screen region for selective capture."""
    x: int
    y: int
    width: int
    height: int

    @property
    def tuple(self) -> Tuple[int, int, int, int]:
        return (self.x, self.y, self.x + self.width, self.y + self.height)

    def contains(self, px: int, py: int) -> bool:
        return (self.x <= px < self.x + self.width and
                self.y <= py < self.y + self.height)


@dataclass
class CaptureResult:
    """Result of a screenshot capture."""
    image: Image.Image
    region: CaptureRegion
    timestamp: float = 0.0
    filename: str = ""
    clipboard_available: bool = False

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


class ScreenCapture:
    """Screenshot tool for the Nyrqis desktop.

    Parameters
    ----------
    session : DesktopSession
        The desktop session to capture.
    default_format : str
        Image format for saves ('png' or 'jpeg').
    default_quality : int
        JPEG quality (1-100, ignored for PNG).
    """

    def __init__(
        self,
        session,
        default_format: str = "png",
        default_quality: int = 95,
    ) -> None:
        self._session = session
        self._default_format = default_format
        self._default_quality = default_quality
        self._history: List[CaptureResult] = []
        self._callbacks: List[Callable] = []
        self._clipboard: Optional[Image.Image] = None
        self._counter = 0

    # -- Capture API --------------------------------------------------

    def grab_fullscreen(
        self,
        delay: float = 0.0,
        monitor_id: Optional[str] = None,
    ) -> CaptureResult:
        """Capture the entire screen (or a specific monitor).

        Parameters
        ----------
        delay : float
            Seconds to wait before capture.  Useful for delayed
            screenshots so you can arrange the screen first.
        monitor_id : str, optional
            Capture only a specific monitor.  If None, captures
            the full virtual desktop.
        """
        if delay > 0:
            time.sleep(delay)

        img = self._session.live_render()

        if monitor_id:
            monitor = None
            for m in self._session.monitors:
                if m.id == monitor_id:
                    monitor = m
                    break
            if monitor:
                img = img.crop((
                    monitor.x, monitor.y,
                    monitor.x + monitor.width,
                    monitor.y + monitor.height,
                ))
                region = CaptureRegion(monitor.x, monitor.y,
                                       monitor.width, monitor.height)
            else:
                region = CaptureRegion(0, 0, img.width, img.height)
        else:
            region = CaptureRegion(0, 0, img.width, img.height)

        result = CaptureResult(
            image=img,
            region=region,
            clipboard_available=True,
        )
        self._history.append(result)
        self._notify("capture", result)
        return result

    def grab_region(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        delay: float = 0.0,
    ) -> CaptureResult:
        """Capture a rectangular region of the screen.

        Parameters
        ----------
        x, y : int
            Top-left corner of the region (screen coordinates).
        width, height : int
            Width and height of the region.
        delay : float
            Seconds to wait before capture.
        """
        if delay > 0:
            time.sleep(delay)

        img = self._session.live_render()
        region = CaptureRegion(x, y, width, height)

        # Crop to the region
        box = region.tuple
        cropped = img.crop(box)

        result = CaptureResult(
            image=cropped,
            region=region,
            clipboard_available=True,
        )
        self._history.append(result)
        self._notify("capture", result)
        return result

    def grab_window(self, window_id: str) -> Optional[CaptureResult]:
        """Capture a specific window.

        Parameters
        ----------
        window_id : str
            The window ID to capture.
        """
        win = None
        for w in self._session.windows:
            if w.id == window_id:
                win = w
                break
        if win is None or win.minimized:
            return None

        return self.grab_region(win.x, win.y, win.width, win.height)

    # -- Save ---------------------------------------------------------

    def save(
        self,
        result: CaptureResult,
        path: str,
        format: Optional[str] = None,
        quality: Optional[int] = None,
    ) -> str:
        """Save a capture to a file.

        Parameters
        ----------
        result : CaptureResult
            The capture to save.
        path : str
            Destination file path.
        format : str, optional
            Image format ('png', 'jpeg').  Defaults to self._default_format.
        quality : int, optional
            JPEG quality (1-100).  Defaults to self._default_quality.

        Returns
        -------
        str
            The absolute path of the saved file.
        """
        fmt = (format or self._default_format).lower()
        qual = quality or self._default_quality

        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

        if fmt in ("jpg", "jpeg"):
            result.image.save(path, "JPEG", quality=qual)
        else:
            result.image.save(path, "PNG")

        result.filename = path
        self._log(f"Saved screenshot to {path}")
        return path

    def save_default(self, result: CaptureResult) -> str:
        """Save with a default filename in the screenshots directory.

        Returns the path of the saved file.
        """
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"nyrqis_screenshot_{ts}.png"
        save_dir = os.path.expanduser("~/Pictures/Nyrqis")
        path = os.path.join(save_dir, filename)
        return self.save(result, path)

    # -- Clipboard ----------------------------------------------------

    def copy_to_clipboard(self, result: CaptureResult) -> None:
        """Copy the captured image to the internal clipboard.

        On a real OS this would use xclip/wl-copy or the Wayland
        clipboard protocol.  On the floor it stores the PIL Image
        internally.
        """
        self._clipboard = result.image.copy()
        self._log("Screenshot copied to clipboard")

    @property
    def clipboard_image(self) -> Optional[Image.Image]:
        """Get the image currently in the clipboard."""
        return self._clipboard

    def paste_from_clipboard(self) -> Optional[Image.Image]:
        """Get the clipboard image (alias for clipboard_image)."""
        return self._clipboard

    # -- History ------------------------------------------------------

    @property
    def history(self) -> List[CaptureResult]:
        """List of all captures in this session."""
        return list(self._history)

    @property
    def last_capture(self) -> Optional[CaptureResult]:
        """The most recent capture, or None."""
        return self._history[-1] if self._history else None

    def clear_history(self) -> int:
        """Clear capture history.  Returns the count cleared."""
        count = len(self._history)
        self._history.clear()
        return count

    # -- Annotations --------------------------------------------------

    def annotate(
        self,
        result: CaptureResult,
        annotations: List[Dict[str, Any]],
    ) -> CaptureResult:
        """Draw annotations on a capture.

        Parameters
        ----------
        result : CaptureResult
            The capture to annotate.
        annotations : list of dict
            Each dict has:
            - type: 'rectangle', 'circle', 'arrow', 'text'
            - x, y: position
            - width, height: size (for rectangle)
            - text: label (for text)
            - color: RGB tuple (default red)
            - width: line width (default 2)

        Returns
        -------
        CaptureResult
            The annotated capture (modifies in-place).
        """
        img = result.image.copy()
        draw = ImageDraw.Draw(img)

        for ann in annotations:
            ann_type = ann.get("type", "rectangle")
            x, y = ann.get("x", 0), ann.get("y", 0)
            color = ann.get("color", (255, 0, 0))
            line_width = ann.get("width", 2)

            if ann_type == "rectangle":
                w, h = ann.get("width", 100), ann.get("height", 100)
                draw.rectangle(
                    [x, y, x + w, y + h],
                    outline=color, width=line_width,
                )
            elif ann_type == "circle":
                w, h = ann.get("width", 100), ann.get("height", 100)
                draw.ellipse(
                    [x, y, x + w, y + h],
                    outline=color, width=line_width,
                )
            elif ann_type == "text":
                text = ann.get("text", "")
                try:
                    font = ImageFont.truetype(
                        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
                except (OSError, IOError):
                    font = ImageFont.load_default()
                draw.text((x, y), text, fill=color, font=font)
            elif ann_type == "arrow":
                x2, y2 = ann.get("x2", x + 100), ann.get("y2", y)
                draw.line([x, y, x2, y2], fill=color, width=line_width)

        result.image = img
        self._notify("annotate", result)
        return result

    # -- Convenience --------------------------------------------------

    def grab_and_save(
        self,
        path: str,
        region: Optional[Tuple[int, int, int, int]] = None,
        delay: float = 0.0,
    ) -> str:
        """Grab and save in one call.

        Parameters
        ----------
        path : str
            Destination file path.
        region : tuple, optional
            (x, y, width, height).  If None, full screen.
        delay : float
            Delay before capture.

        Returns
        -------
        str
            The saved file path.
        """
        if region:
            result = self.grab_region(*region, delay=delay)
        else:
            result = self.grab_fullscreen(delay=delay)
        return self.save(result, path)

    # -- Callbacks ----------------------------------------------------

    def on_capture(self, callback: Callable) -> None:
        """Register a callback for capture events."""
        self._callbacks.append(callback)

    # -- Internal -----------------------------------------------------

    def _notify(self, event: str, result: CaptureResult) -> None:
        for cb in self._callbacks:
            try:
                cb(event, result)
            except Exception as e:
                self._log(f"Callback error: {e}")

    def _log(self, msg: str) -> None:
        logger.info("[Screenshot] %s", msg)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """Run the screenshot tool standalone (for testing)."""
    import sys
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.abspath(__file__)), ".."))

    from ui.nstudio import loads
    from ui.desktop_session import DesktopSession
    import json

    # Create a minimal session
    raw = {
        "version": "1.0.0",
        "project": {"name": "screenshot-test"},
        "themes": {"active": "Eclipse"},
        "states": {},
        "stateScopes": {},
        "locales": {},
        "resources": {},
        "animations": [],
        "behaviors": [],
        "bindings": [],
        "components": [],
        "screens": [{
            "id": "desktop",
            "size": {"width": 1920, "height": 1080},
            "root": {
                "id": "root",
                "type": "DesktopSurface",
                "layout": {"x": 0, "y": 0, "width": 1920, "height": 1080},
                "children": [],
            },
        }],
    }
    doc = loads(json.dumps(raw))
    session = DesktopSession(doc)

    capture = ScreenCapture(session)

    print("=== Nyrqis Screenshot Tool ===")

    # Full screen capture
    result = capture.grab_fullscreen()
    print(f"Captured: {result.region.width}x{result.region.height}")
    print(f"Timestamp: {result.timestamp}")

    # Region capture
    region_result = capture.grab_region(100, 100, 800, 600)
    print(f"Region capture: {region_result.region.width}x{region_result.region.height}")

    # Clipboard
    capture.copy_to_clipboard(result)
    print(f"Clipboard has image: {capture.clipboard_image is not None}")

    # History
    print(f"Capture history: {len(capture.history)}")

    # Save
    save_path = "/tmp/nyrqis_test_screenshot.png"
    saved = capture.save(result, save_path)
    print(f"Saved to: {saved}")

    # Annotate
    annotated = capture.annotate(result, [
        {"type": "rectangle", "x": 100, "y": 100, "width": 200, "height": 100},
        {"type": "text", "x": 110, "y": 110, "text": "Test annotation"},
    ])
    print(f"Annotated image size: {annotated.image.size}")

    # Clear history
    cleared = capture.clear_history()
    print(f"Cleared {cleared} captures from history")

    print("\nAll screenshot operations passed!")


if __name__ == "__main__":
    main()
