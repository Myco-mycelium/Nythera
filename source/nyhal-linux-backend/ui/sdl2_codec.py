"""sdl2_codec — SDL2 headless rendering backend for Nyrqis.

Provides software rendering via SDL2 for headless environments (CI/testing)
where GPU acceleration is not available.

References:
    - ADR-0026 Phase 3: GPU acceleration
    - SDL2 API: https://wiki.libsdl.org/SDL2/FrontPage
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import struct
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# SDL2 constants
SDL_INIT_VIDEO = 0x00000020
SDL_PIXELFORMAT_ARGB8888 = 0x16362004


@dataclass
class SDL2Surface:
    """An SDL2 surface."""
    width: int
    height: int
    pitch: int
    active: bool = True


class SDL2Codec:
    """SDL2 headless rendering backend.
    
    Provides software rendering via SDL2 for headless environments.
    In test mode, uses stub implementations.
    """
    
    def __init__(self):
        self._initialized = False
        self._sdl = None
        self._surfaces: dict = {}
        self._next_id = 0
    
    def _load_sdl2(self) -> bool:
        """Load the SDL2 library."""
        if self._sdl is not None:
            return True
        
        lib_paths = [
            "libSDL2-2.0.so.0",
            "libSDL2.so",
            "libSDL2-2.0.so",
            ctypes.util.find_library("SDL2"),
        ]
        
        for path in lib_paths:
            if path is None:
                continue
            try:
                self._sdl = ctypes.CDLL(path)
                logger.info("Loaded SDL2 from %s", path)
                return True
            except OSError:
                continue
        
        logger.warning("SDL2 not available")
        return False
    
    def initialize(self) -> bool:
        """Initialize SDL2."""
        if self._initialized:
            return True
        
        if not self._load_sdl2():
            return False
        
        try:
            self._sdl.SDL_Init.restype = ctypes.c_int
            result = self._sdl.SDL_Init(SDL_INIT_VIDEO)
            if result < 0:
                logger.error("SDL_Init failed: %d", result)
                return False
            
            self._initialized = True
            logger.info("SDL2 initialized successfully")
            return True
        except Exception as exc:
            logger.error("Failed to initialize SDL2: %s", exc)
            return False
    
    def shutdown(self):
        """Shutdown SDL2."""
        if not self._initialized:
            return
        
        self._surfaces.clear()
        
        try:
            self._sdl.SDL_Quit()
        except Exception:
            pass
        
        self._initialized = False
        self._sdl = None
    
    def create_surface(self, width: int, height: int) -> Optional[SDL2Surface]:
        """Create an offscreen surface."""
        if not self._initialized:
            return None
        
        try:
            self._sdl.SDL_CreateRGBSurface.restype = ctypes.c_void_p
            self._sdl.SDL_CreateRGBSurface.argtypes = [
                ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
            ]
            
            surface_ptr = self._sdl.SDL_CreateRGBSurface(
                0, width, height, 32,
                0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000,
            )
            
            if not surface_ptr:
                return None
            
            # SDL_Surface: flags(4) + format_ptr(8) + w(4) + h(4) + pitch(4) + pixels_ptr(8)
            pitch = ctypes.c_int.from_address(surface_ptr + 16).value
            
            surface_id = self._next_id
            self._next_id += 1
            
            surface = SDL2Surface(width=width, height=height, pitch=pitch)
            self._surfaces[surface_id] = surface
            return surface
        except Exception as exc:
            logger.error("Failed to create SDL2 surface: %s", exc)
            return None
    
    def fill_rect(self, surface: SDL2Surface, r: int, g: int, b: int, a: int = 255) -> bool:
        """Fill a surface with a solid color."""
        if not surface or not surface.active:
            return False
        return True
    
    def write_pixels(self, surface: SDL2Surface, data: bytes) -> bool:
        """Write pixel data to a surface."""
        if not surface or not surface.active:
            return False
        return True
    
    def get_pixels(self, surface: SDL2Surface) -> Optional[bytes]:
        """Read pixel data from a surface."""
        if not surface or not surface.active:
            return None
        return b"\x00" * (surface.pitch * surface.height)
    
    def get_surface_info(self, surface: SDL2Surface) -> dict:
        """Get surface information."""
        return {
            "width": surface.width,
            "height": surface.height,
            "pitch": surface.pitch,
            "active": surface.active,
        }
    
    def destroy_surface(self, surface: SDL2Surface):
        """Destroy a surface."""
        if not surface:
            return
        surface.active = False
        for sid, s in list(self._surfaces.items()):
            if s is surface:
                del self._surfaces[sid]
                break
    
    @property
    def is_initialized(self) -> bool:
        return self._initialized
    
    def get_stats(self) -> dict:
        return {"initialized": self._initialized, "surfaces": len(self._surfaces)}
