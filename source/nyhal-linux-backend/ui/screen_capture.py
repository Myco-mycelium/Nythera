#!/usr/bin/env python3
"""Screen capture tool for the Nyrqis desktop.

Features:
- Single frame capture
- Region capture (rectangular selection)
- Timed capture (every N milliseconds)
- Frame sequence recording
- Export to PNG files
- Overlay indicators for recording state
"""

from __future__ import annotations

import os
import struct
import time
import zlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Capture modes
# ---------------------------------------------------------------------------

class CaptureMode(Enum):
    """Screen capture modes."""
    FULL = "full"
    REGION = "region"
    WINDOW = "window"
    TIMED = "timed"


class RecordingState(Enum):
    """Recording states."""
    IDLE = "idle"
    RECORDING = "recording"
    PAUSED = "paused"


# ---------------------------------------------------------------------------
# Capture region
# ---------------------------------------------------------------------------

@dataclass
class CaptureRegion:
    """A rectangular region for capture."""
    x: int = 0
    y: int = 0
    width: int = 1920
    height: int = 1080
    
    @property
    def rect(self) -> Tuple[int, int, int, int]:
        return (self.x, self.y, self.width, self.height)
    
    @property
    def area(self) -> int:
        return self.width * self.height
    
    def contains(self, px: int, py: int) -> bool:
        return self.x <= px < self.x + self.width and self.y <= py < self.y + self.height


# ---------------------------------------------------------------------------
# Frame data
# ---------------------------------------------------------------------------

@dataclass
class Frame:
    """A single captured frame."""
    frame_id: int
    timestamp: float
    width: int
    height: int
    rgb_data: bytes  # Raw RGB bytes
    region: Optional[CaptureRegion] = None
    
    @property
    def size(self) -> int:
        return len(self.rgb_data)
    
    def save_png(self, path: str) -> None:
        """Save the frame as a PNG file."""
        _write_png(path, self.width, self.height, self.rgb_data)


# ---------------------------------------------------------------------------
# Screen capture
# ---------------------------------------------------------------------------

class ScreenCapture:
    """Screen capture and recording tool.
    
    Parameters
    ----------
    screen_width : int
        Screen width.
    screen_height : int
        Screen height.
    """
    
    # Recording indicator colors
    REC_DOT_COLOR = (220, 50, 50)
    REC_BG_COLOR = (40, 40, 50, 200)
    
    def __init__(self, screen_width: int = 1920, screen_height: int = 1080):
        self._sw = screen_width
        self._sh = screen_height
        
        # Capture state
        self._mode: CaptureMode = CaptureMode.FULL
        self._region: CaptureRegion = CaptureRegion(0, 0, screen_width, screen_height)
        self._state: RecordingState = RecordingState.IDLE
        
        # Recording
        self._frames: List[Frame] = []
        self._frame_count: int = 0
        self._record_start: float = 0.0
        self._record_interval_ms: int = 100  # ms between frames
        self._last_frame_time: float = 0.0
        self._max_frames: int = 10000
        
        # Timed capture
        self._timed_interval_ms: int = 1000
        self._timed_count: int = 0
        self._timed_max: int = 100
        
        # Callbacks
        self._on_frame: List[Callable] = []
        self._on_record_start: List[Callable] = []
        self._on_record_stop: List[Callable] = []
    
    # -- Properties --------------------------------------------------------
    
    @property
    def mode(self) -> CaptureMode:
        return self._mode
    
    @property
    def state(self) -> RecordingState:
        return self._state
    
    @property
    def is_recording(self) -> bool:
        return self._state == RecordingState.RECORDING
    
    @property
    def frame_count(self) -> int:
        return self._frame_count
    
    @property
    def frames(self) -> List[Frame]:
        return list(self._frames)
    
    @property
    def duration_ms(self) -> float:
        if self._state == RecordingState.RECORDING:
            return (time.time() - self._record_start) * 1000
        return 0.0
    
    @property
    def region(self) -> CaptureRegion:
        return self._region
    
    # -- Capture operations ------------------------------------------------
    
    def capture_full(self, desktop_pixels: List[Tuple[int, int, int]],
                     width: int, height: int) -> Frame:
        """Capture the full screen."""
        rgb_data = bytearray(width * height * 3)
        i = 0
        for r, g, b in desktop_pixels:
            rgb_data[i] = r
            rgb_data[i+1] = g
            rgb_data[i+2] = b
            i += 3
        
        self._frame_count += 1
        frame = Frame(
            frame_id=self._frame_count,
            timestamp=time.time(),
            width=width,
            height=height,
            rgb_data=bytes(rgb_data),
        )
        
        self._dispatch_frame(frame)
        return frame
    
    def capture_region(self, desktop_pixels: List[Tuple[int, int, int]],
                      screen_width: int, region: CaptureRegion) -> Frame:
        """Capture a specific region."""
        rgb_data = bytearray(region.width * region.height * 3)
        i = 0
        for y in range(region.y, min(region.y + region.height, len(desktop_pixels) // screen_width)):
            for x in range(region.x, min(region.x + region.width, screen_width)):
                idx = y * screen_width + x
                if idx < len(desktop_pixels):
                    r, g, b = desktop_pixels[idx]
                    rgb_data[i] = r
                    rgb_data[i+1] = g
                    rgb_data[i+2] = b
                    i += 3
        
        self._frame_count += 1
        frame = Frame(
            frame_id=self._frame_count,
            timestamp=time.time(),
            width=region.width,
            height=region.height,
            rgb_data=bytes(rgb_data[:i]),
            region=region,
        )
        
        self._dispatch_frame(frame)
        return frame
    
    def capture_window(self, desktop_pixels: List[Tuple[int, int, int]],
                      screen_width: int,
                      win_x: int, win_y: int, win_w: int, win_h: int) -> Frame:
        """Capture a window."""
        region = CaptureRegion(win_x, win_y, win_w, win_h)
        return self.capture_region(desktop_pixels, screen_width, region)
    
    def set_region(self, x: int, y: int, width: int, height: int) -> None:
        """Set the capture region."""
        self._region = CaptureRegion(
            max(0, x), max(0, y),
            min(width, self._sw - x), min(height, self._sh - y)
        )
    
    # -- Recording ---------------------------------------------------------
    
    def start_recording(self, interval_ms: int = 100) -> None:
        """Start recording frames."""
        self._state = RecordingState.RECORDING
        self._record_start = time.time()
        self._record_interval_ms = interval_ms
        self._last_frame_time = 0.0
        self._frames.clear()
        for cb in self._on_record_start:
            cb()
    
    def stop_recording(self) -> List[Frame]:
        """Stop recording and return captured frames."""
        self._state = RecordingState.IDLE
        frames = list(self._frames)
        for cb in self._on_record_stop:
            cb(frames)
        return frames
    
    def pause_recording(self) -> None:
        """Pause recording."""
        if self._state == RecordingState.RECORDING:
            self._state = RecordingState.PAUSED
    
    def resume_recording(self) -> None:
        """Resume recording."""
        if self._state == RecordingState.PAUSED:
            self._state = RecordingState.RECORDING
    
    def should_capture_frame(self) -> bool:
        """Check if it's time to capture a frame during recording."""
        if self._state != RecordingState.RECORDING:
            return False
        
        now = time.time()
        elapsed = (now - self._record_start) * 1000
        
        if elapsed - self._last_frame_time >= self._record_interval_ms:
            self._last_frame_time = elapsed
            return True
        
        return False
    
    def add_frame(self, frame: Frame) -> None:
        """Add a frame to the recording buffer."""
        if len(self._frames) < self._max_frames:
            self._frames.append(frame)
    
    # -- Timed capture -----------------------------------------------------
    
    def start_timed_capture(self, interval_ms: int = 1000, count: int = 10) -> None:
        """Start timed capture mode."""
        self._mode = CaptureMode.TIMED
        self._timed_interval_ms = interval_ms
        self._timed_max = count
        self._timed_count = 0
    
    def should_timed_capture(self) -> bool:
        """Check if it's time for a timed capture."""
        if self._mode != CaptureMode.TIMED:
            return False
        return self._timed_count < self._timed_max
    
    def increment_timed_count(self) -> None:
        self._timed_count += 1
    
    # -- Overlay rendering -------------------------------------------------
    
    def render_recording_overlay(self, pixels: List[Tuple[int, int, int]],
                                 width: int, height: int) -> List[Tuple[int, int, int]]:
        """Render recording indicator overlay on the pixel buffer."""
        if self._state != RecordingState.RECORDING:
            return pixels
        
        result = list(pixels)
        
        def set_pixel(px: int, py: int, color: Tuple[int, int, int]) -> None:
            if 0 <= px < width and 0 <= py < height:
                result[py * width + px] = color
        
        def fill_rect(rx: int, ry: int, rw: int, rh: int, color: Tuple[int, int, int]) -> None:
            for dy in range(rh):
                for dx in range(rw):
                    set_pixel(rx + dx, ry + dy, color)
        
        # Recording dot (pulsing)
        import math
        pulse = abs(math.sin(time.time() * 3))
        dot_color = (
            int(self.REC_DOT_COLOR[0] * pulse),
            int(self.REC_DOT_COLOR[1] * pulse),
            int(self.REC_DOT_COLOR[2] * pulse),
        )
        
        # Top-right indicator
        indicator_x = width - 100
        indicator_y = 12
        fill_rect(indicator_x - 4, indicator_y - 4, 120, 28, (30, 30, 40))
        fill_rect(indicator_x, indicator_y, 16, 16, dot_color)
        
        # Frame count text (simple digits)
        frame_text = str(self._frame_count)
        tx = indicator_x + 24
        for ch in frame_text:
            digit = ord(ch) - ord('0')
            _draw_digit(set_pixel, tx, indicator_y, digit, self.REC_DOT_COLOR)
            tx += 10
        
        return result
    
    def render_region_overlay(self, pixels: List[Tuple[int, int, int]],
                             width: int, height: int) -> List[Tuple[int, int, int]]:
        """Render capture region overlay."""
        if self._mode != CaptureMode.REGION:
            return pixels
        
        result = list(pixels)
        
        def set_pixel(px: int, py: int, color: Tuple[int, int, int]) -> None:
            if 0 <= px < width and 0 <= py < height:
                result[py * width + px] = color
        
        # Draw region border (dashed)
        r = self._region
        border_color = (80, 140, 255)
        
        # Top/bottom edges
        for x in range(r.x, r.x + r.width):
            if (x // 4) % 2 == 0:  # Dashed
                set_pixel(x, r.y, border_color)
                set_pixel(x, r.y + r.height - 1, border_color)
        
        # Left/right edges
        for y in range(r.y, r.y + r.height):
            if (y // 4) % 2 == 0:
                set_pixel(r.x, y, border_color)
                set_pixel(r.x + r.width - 1, y, border_color)
        
        return result
    
    # -- Export ------------------------------------------------------------
    
    def export_frames(self, output_dir: str, prefix: str = "frame") -> List[str]:
        """Export all recorded frames as PNG files."""
        os.makedirs(output_dir, exist_ok=True)
        paths = []
        
        for i, frame in enumerate(self._frames):
            path = os.path.join(output_dir, f"{prefix}_{i:04d}.png")
            frame.save_png(path)
            paths.append(path)
        
        return paths
    
    def export_single(self, path: str, frame: Frame) -> None:
        """Export a single frame as PNG."""
        frame.save_png(path)
    
    # -- Callbacks ---------------------------------------------------------
    
    def on_frame(self, callback: Callable) -> None:
        self._on_frame.append(callback)
    
    def on_record_start(self, callback: Callable) -> None:
        self._on_record_start.append(callback)
    
    def on_record_stop(self, callback: Callable) -> None:
        self._on_record_stop.append(callback)
    
    def _dispatch_frame(self, frame: Frame) -> None:
        for cb in self._on_frame:
            cb(frame)
    
    def __repr__(self) -> str:
        return (
            f"ScreenCapture(mode={self._mode.value}, "
            f"state={self._state.value}, "
            f"frames={len(self._frames)})"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _draw_digit(set_pixel: Callable, x: int, y: int, digit: int,
                color: Tuple[int, int, int]) -> None:
    """Draw a simple 3x5 digit."""
    DIGITS = [
        [0x7, 0x5, 0x5, 0x5, 0x7],  # 0
        [0x2, 0x6, 0x2, 0x2, 0x7],  # 1
        [0x7, 0x1, 0x7, 0x4, 0x7],  # 2
        [0x7, 0x1, 0x7, 0x1, 0x7],  # 3
        [0x5, 0x5, 0x7, 0x1, 0x1],  # 4
        [0x7, 0x4, 0x7, 0x1, 0x7],  # 5
        [0x7, 0x4, 0x7, 0x5, 0x7],  # 6
        [0x7, 0x1, 0x2, 0x2, 0x2],  # 7
        [0x7, 0x5, 0x7, 0x5, 0x7],  # 8
        [0x7, 0x5, 0x7, 0x1, 0x7],  # 9
    ]
    
    glyph = DIGITS[digit % 10]
    for row in range(5):
        for col in range(3):
            if glyph[row] & (4 >> col):
                set_pixel(x + col, y + row, color)


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
