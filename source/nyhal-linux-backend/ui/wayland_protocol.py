"""wayland_protocol — Wayland protocol message serialization/deserialization.

Provides encoding and decoding of Wayland protocol messages for the
compositor. This handles the binary wire format of the Wayland protocol:

1. Message encoding (server → client)
2. Message decoding (client → server)
3. Event construction
4. Request parsing

The Wayland wire format is:
- Object ID (4 bytes, uint32)
- Size + Opcode (4 bytes, uint32 upper 16 = size, lower 16 = opcode)
- Arguments (variable, padded to 4 bytes)

References:
    - Wayland protocol: https://wayland.freedesktop.org/docs/html/
    - ADR-0026: Wayland display-server integration
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Dict, List, Optional, Tuple


class ObjectType(IntEnum):
    """Wayland object types."""
    WL_DISPLAY = 1
    WL_REGISTRY = 2
    WL_CALLBACK = 3
    WL_COMPOSITOR = 4
    WL_SHM_POOL = 5
    WL_BUFFER = 6
    WL_SHM = 7
    WL_DATA_DEVICE_MANAGER = 8
    WL_DATA_DEVICE = 9
    WL_DATA_SOURCE = 10
    WL_SEAT = 11
    WL_POINTER = 12
    WL_KEYBOARD = 13
    WL_TOUCH = 14
    WL_OUTPUT = 15
    WL_REGION = 16
    WL_SUBCOMPOSITOR = 17
    WL_SUBSURFACE = 18
    XDG_WM_BASE = 19
    XDG_SURFACE = 20
    XDG_TOPLEVEL = 21
    XDG_POPUP = 22
    XDG_POSITIONER = 23


class WLEvent(IntEnum):
    """Wayland server events (opcode)."""
    WL_DISPLAY_ERROR = 0
    WL_DISPLAY_DELETE_ID = 2
    WL_REGISTRY_GLOBAL = 0
    WL_REGISTRY_GLOBAL_REMOVE = 1
    WL_CALLBACK_DONE = 0
    WL_COMPOSITOR_CREATE_SURFACE = 0
    WL_COMPOSITOR_CREATE_REGION = 1
    WL_SHM_FORMAT = 0
    WL_BUFFER_RELEASE = 0
    WL_OUTPUT_GEOMETRY = 0
    WL_OUTPUT_MODE = 1
    WL_OUTPUT_DONE = 3
    WL_OUTPUT_SCALE = 4
    WL_SEAT_CAPABILITIES = 0
    WL_SEAT_NAME = 1
    WL_POINTER_ENTER = 0
    WL_POINTER_LEAVE = 1
    WL_POINTER_MOTION = 2
    WL_POINTER_BUTTON = 3
    WL_POINTER_AXIS = 4
    WL_KEYBOARD_KEYMAP = 0
    WL_KEYBOARD_ENTER = 1
    WL_KEYBOARD_LEAVE = 2
    WL_KEYBOARD_KEY = 3
    WL_KEYBOARD_MODIFIERS = 4
    WL_SURFACE_ENTER = 0
    WL_SURFACE_LEAVE = 1
    WL_REGION_CREATE = 0
    WL_REGION_DESTROY = 1
    WL_REGION_ADD = 2
    WL_REGION_SUBTRACT = 3
    XDG_WM_BASE_PING = 0
    XDG_WM_BASE_GET_XDG_SURFACE = 2
    XDG_WM_BASE_GET_XDG_POPUP = 3
    XDG_SURFACE_GET_TOPLEVEL = 1
    XDG_SURFACE_GET_POPUP = 2
    XDG_SURFACE_SET_WINDOW_GEOMETRY = 3
    XDG_SURFACE_ACK_CONFIGURE = 4
    XDG_TOPLEVEL_DESTROY = 0
    XDG_TOPLEVEL_SET_TITLE = 2
    XDG_TOPLEVEL_SET_APP_ID = 3
    XDG_TOPLEVEL_SET_MIN_SIZE = 5
    XDG_TOPLEVEL_SET_MAX_SIZE = 6
    XDG_TOPLEVEL_SET_MINIMIZE = 7
    XDG_TOPLEVEL_SET_MAXIMIZE = 8
    XDG_TOPLEVEL_UNSET_MAXIMIZE = 9
    XDG_TOPLEVEL_SET_FULLSCREEN = 10
    XDG_TOPLEVEL_UNSET_FULLSCREEN = 11
    XDG_TOPLEVEL_SET_MOVING = 14
    XDG_TOPLEVEL_SET_RESIZING = 15
    XDG_TOPLEVEL_SET_ACTIVATED = 17
    XDG_TOPLEVEL_SET_CLOSED = 19
    XDG_TOPLEVEL_CONFIGURE = 0
    XDG_TOPLEVEL_CLOSE = 1
    XDG_TOPLEVEL_WM_CONFIGURE = 2


@dataclass
class WaylandMessage:
    """A decoded Wayland protocol message."""
    object_id: int
    opcode: int
    size: int
    args: List[Any]


class WaylandEncoder:
    """Encodes Wayland protocol messages (server → client)."""
    
    @staticmethod
    def encode_message(object_id: int, opcode: int, *args) -> bytes:
        """Encode a Wayland message.
        
        Parameters
        ----------
        object_id : int
            Target object ID.
        opcode : int
            Message opcode.
        *args : mixed
            Message arguments (int, str, fixed, array, fd).
            
        Returns
        -------
        bytes
            Encoded message.
        """
        payload = WaylandEncoder._encode_args(args)
        size = 8 + len(payload)  # header + payload
        
        # Header: object_id (4) + size_opcode (4)
        header = struct.pack("II", object_id, (size << 16) | (opcode & 0xFFFF))
        
        return header + payload
    
    @staticmethod
    def _encode_args(args: tuple) -> bytes:
        """Encode message arguments."""
        result = b""
        
        for arg in args:
            if isinstance(arg, int):
                # 32-bit integer
                result += struct.pack("i", arg)
            elif isinstance(arg, str):
                # String (null-terminated, padded to 4 bytes)
                encoded = arg.encode("utf-8") + b"\x00"
                result += struct.pack("I", len(encoded))
                result += encoded
                # Pad to 4-byte alignment
                while len(result) % 4:
                    result += b"\x00"
            elif isinstance(arg, bytes):
                # Array (length + data)
                result += struct.pack("I", len(arg))
                result += arg
                # Pad to 4-byte alignment
                while len(result) % 4:
                    result += b"\x00"
            elif isinstance(arg, float):
                # Fixed-point (24.8)
                fixed = int(arg * 256)
                result += struct.pack("i", fixed)
        
        return result
    
    @staticmethod
    def encode_registry_global(object_id: int, name: int,
                               interface: str, version: int) -> bytes:
        """Encode wl_registry.global event."""
        return WaylandEncoder.encode_message(
            object_id, WLEvent.WL_REGISTRY_GLOBAL,
            name, interface, version,
        )
    
    @staticmethod
    def encode_callback_done(object_id: int, callback_id: int,
                            timestamp: int) -> bytes:
        """Encode wl_callback.done event."""
        return WaylandEncoder.encode_message(
            object_id, WLEvent.WL_CALLBACK_DONE,
            timestamp,
        )
    
    @staticmethod
    def encode_output_geometry(object_id: int, output_id: int,
                               x: int, y: int, w: int, h: int,
                               subpixel: int, make: str, model: str,
                               transform: int) -> bytes:
        """Encode wl_output.geometry event."""
        return WaylandEncoder.encode_message(
            output_id, WLEvent.WL_OUTPUT_GEOMETRY,
            x, y, w, h, subpixel, make, model, transform,
        )
    
    @staticmethod
    def encode_output_mode(object_id: int, output_id: int,
                           flags: int, width: int, height: int,
                           refresh: int) -> bytes:
        """Encode wl_output.mode event."""
        return WaylandEncoder.encode_message(
            output_id, WLEvent.WL_OUTPUT_MODE,
            flags, width, height, refresh,
        )
    
    @staticmethod
    def encode_output_done(object_id: int, output_id: int) -> bytes:
        """Encode wl_output.done event."""
        return WaylandEncoder.encode_message(
            output_id, WLEvent.WL_OUTPUT_DONE,
        )
    
    @staticmethod
    def encode_xdg_wm_base_ping(object_id: int, serial: int) -> bytes:
        """Encode xdg_wm_base.ping event."""
        return WaylandEncoder.encode_message(
            object_id, WLEvent.XDG_WM_BASE_PING,
            serial,
        )
    
    @staticmethod
    def encode_xdg_toplevel_configure(object_id: int, width: int,
                                      height: int, states: bytes) -> bytes:
        """Encode xdg_toplevel.configure event."""
        return WaylandEncoder.encode_message(
            object_id, WLEvent.XDG_TOPLEVEL_CONFIGURE,
            width, height, states,
        )
    
    @staticmethod
    def encode_xdg_toplevel_close(object_id: int) -> bytes:
        """Encode xdg_toplevel.close event."""
        return WaylandEncoder.encode_message(
            object_id, WLEvent.XDG_TOPLEVEL_CLOSE,
        )
    
    @staticmethod
    def encode_surface_enter(object_id: int, output_id: int) -> bytes:
        """Encode wl_surface.enter event."""
        return WaylandEncoder.encode_message(
            object_id, WLEvent.WL_SURFACE_ENTER,
            output_id,
        )
    
    @staticmethod
    def encode_surface_leave(object_id: int, output_id: int) -> bytes:
        """Encode wl_surface.leave event."""
        return WaylandEncoder.encode_message(
            object_id, WLEvent.WL_SURFACE_LEAVE,
            output_id,
        )


class WaylandDecoder:
    """Decodes Wayland protocol messages (client → server)."""
    
    @staticmethod
    def decode_message(data: bytes) -> Optional[WaylandMessage]:
        """Decode a Wayland message.
        
        Parameters
        ----------
        data : bytes
            Raw message data.
            
        Returns
        -------
        WaylandMessage or None
            Decoded message, or None if invalid.
        """
        if len(data) < 8:
            return None
        
        # Parse header
        object_id = struct.unpack("I", data[0:4])[0]
        size_opcode = struct.unpack("I", data[4:8])[0]
        size = size_opcode >> 16
        opcode = size_opcode & 0xFFFF
        
        # Parse payload
        payload = data[8:size] if size > 8 else b""
        args = WaylandDecoder._decode_args(payload)
        
        return WaylandMessage(
            object_id=object_id,
            opcode=opcode,
            size=size,
            args=args,
        )
    
    @staticmethod
    def _decode_args(data: bytes) -> List[Any]:
        """Decode message arguments."""
        args = []
        offset = 0
        
        while offset < len(data):
            if offset + 4 > len(data):
                break
            
            # Read 32-bit value
            value = struct.unpack("i", data[offset:offset+4])[0]
            args.append(value)
            offset += 4
        
        return args
    
    @staticmethod
    def decode_string_arg(data: bytes, offset: int) -> Tuple[str, int]:
        """Decode a string argument starting at offset."""
        if offset + 4 > len(data):
            return "", offset
        
        length = struct.unpack("I", data[offset:offset+4])[0]
        offset += 4
        
        if offset + length > len(data):
            return "", offset
        
        string_data = data[offset:offset+length]
        offset += length
        
        # Pad to 4-byte alignment
        while offset % 4:
            offset += 1
        
        return string_data.rstrip(b"\x00").decode("utf-8", errors="replace"), offset
    
    @staticmethod
    def decode_array_arg(data: bytes, offset: int) -> Tuple[bytes, int]:
        """Decode an array argument starting at offset."""
        if offset + 4 > len(data):
            return b"", offset
        
        length = struct.unpack("I", data[offset:offset+4])[0]
        offset += 4
        
        if offset + length > len(data):
            return b"", offset
        
        array_data = data[offset:offset+length]
        offset += length
        
        # Pad to 4-byte alignment
        while offset % 4:
            offset += 1
        
        return array_data, offset
    
    @staticmethod
    def get_message_name(opcode: int) -> str:
        """Get human-readable name for a request opcode."""
        names = {
            0: "error",
            1: "create_surface",
            2: "create_region",
            3: "get_registry",
            4: "sync",
        }
        return names.get(opcode, f"unknown_{opcode}")
