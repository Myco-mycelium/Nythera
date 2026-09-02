"""wayland_client — Wayland client test harness for CI testing.

Provides a mock Wayland client that can connect to the Nyrqis compositor
and exchange protocol messages. This is used for automated testing of the
compositor without requiring real Wayland client applications.

The client can:
1. Connect to the compositor via Unix domain socket
2. Send Wayland protocol requests
3. Receive Wayland protocol events
4. Create and manage surfaces
5. Submit buffer content via SHM

References:
    - ADR-0026: Wayland display-server integration
    - Wayland protocol: https://wayland.freedesktop.org/docs/html/
"""

from __future__ import annotations

import logging
import os
import socket
import struct
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class WaylandEvent:
    """A Wayland event received from the compositor."""
    object_id: int
    opcode: int
    size: int
    data: bytes
    timestamp: float = field(default_factory=time.time)


class WaylandClientTest:
    """Test Wayland client for automated CI testing.
    
    Usage:
        client = WaylandClientTest("/tmp/wayland-0")
        client.connect()
        
        # Perform handshake
        client.sync()
        client.get_registry()
        
        # Create surface
        surface = client.create_surface(800, 600)
        
        # Submit buffer
        client.attach_buffer(surface, pixel_data)
        client.commit(surface)
        
        # Receive events
        events = client.receive_events(timeout=0.5)
        
        client.disconnect()
    """
    
    def __init__(self, socket_path: str = "/tmp/wayland-0"):
        self.socket_path = socket_path
        self._socket: Optional[socket.socket] = None
        self._connected = False
        self._events: List[WaylandEvent] = []
        self._lock = threading.Lock()
        
        # Wayland object IDs
        self._wl_display = 1
        self._wl_registry: Optional[int] = None
        self._wl_compositor: Optional[int] = None
        self._wl_shm: Optional[int] = None
        self._wl_shm_pool: Optional[int] = None
        self._wl_output: Optional[int] = None
        self._wl_seat: Optional[int] = None
        
        # Surface tracking
        self._next_surface_id = 100
        self._next_object_id = 200
        
        # Callbacks
        self._on_event: Optional[Callable] = None
    
    def connect(self) -> bool:
        """Connect to the compositor.
        
        Returns True on success, False on failure.
        """
        try:
            self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._socket.connect(self.socket_path)
            self._connected = True
            logger.info("Connected to compositor: %s", self.socket_path)
            return True
        except (ConnectionRefusedError, FileNotFoundError, OSError) as exc:
            logger.error("Failed to connect to compositor: %s", exc)
            return False
    
    def disconnect(self):
        """Disconnect from the compositor."""
        if self._socket:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None
        self._connected = False
        logger.info("Disconnected from compositor")
    
    def send_request(self, object_id: int, opcode: int, *args) -> bool:
        """Send a Wayland request.
        
        Parameters
        ----------
        object_id : int
            Target object ID.
        opcode : int
            Request opcode.
        *args : mixed
            Request arguments.
            
        Returns
        -------
        bool
            True on success, False on failure.
        """
        if not self._connected:
            return False
        
        # Encode message
        from ui.wayland_protocol import WaylandEncoder
        data = WaylandEncoder.encode_message(object_id, opcode, *args)
        
        try:
            self._socket.sendall(data)
            return True
        except OSError:
            return False
    
    def receive_events(self, timeout: float = 0.1) -> List[WaylandEvent]:
        """Receive events from the compositor.
        
        Parameters
        ----------
        timeout : float
            Timeout in seconds.
            
        Returns
        -------
        list of WaylandEvent
            Received events.
        """
        if not self._connected:
            return []
        
        events = []
        self._socket.settimeout(timeout)
        
        try:
            while True:
                data = self._socket.recv(4096)
                if not data:
                    break
                
                # Parse events
                offset = 0
                while offset + 8 <= len(data):
                    object_id = struct.unpack("I", data[offset:offset+4])[0]
                    size_opcode = struct.unpack("I", data[offset+4:offset+8])[0]
                    size = size_opcode >> 16
                    opcode = size_opcode & 0xFFFF
                    
                    if size < 8 or offset + size > len(data):
                        break
                    
                    event_data = data[offset:offset+size]
                    event = WaylandEvent(
                        object_id=object_id,
                        opcode=opcode,
                        size=size,
                        data=event_data,
                    )
                    events.append(event)
                    
                    # Handle specific events
                    self._handle_event(event)
                    
                    offset += size
        except socket.timeout:
            pass
        
        with self._lock:
            self._events.extend(events)
        
        return events
    
    def _handle_event(self, event: WaylandEvent):
        """Handle a received event."""
        if self._on_event:
            self._on_event(event)
    
    def set_event_callback(self, callback: Callable):
        """Set callback for received events."""
        self._on_event = callback
    
    # Wayland protocol methods
    
    def sync(self) -> bool:
        """Send wl_display.sync request."""
        callback_id = self._alloc_object_id()
        return self.send_request(self._wl_display, 0, callback_id)
    
    def get_registry(self) -> bool:
        """Send wl_display.get_registry request."""
        self._wl_registry = self._alloc_object_id()
        return self.send_request(self._wl_display, 1, self._wl_registry)
    
    def bind_global(self, name: int, interface: str, version: int) -> int:
        """Bind a global object from the registry.
        
        Returns the new object ID.
        """
        new_id = self._alloc_object_id()
        
        # Map interface to known objects
        if interface == "wl_compositor":
            self._wl_compositor = new_id
        elif interface == "wl_shm":
            self._wl_shm = new_id
        elif interface == "wl_output":
            self._wl_output = new_id
        elif interface == "wl_seat":
            self._wl_seat = new_id
        
        # Send wl_registry.bind request
        # Object ID, name, interface string, version, new_id
        self.send_request(self._wl_registry, 0, name, interface, version, new_id)
        
        return new_id
    
    def create_surface(self, width: int = 800, height: int = 600) -> int:
        """Create a new Wayland surface.
        
        Returns the surface ID.
        """
        if self._wl_compositor is None:
            logger.error("wl_compositor not bound")
            return -1
        
        surface_id = self._next_surface_id
        self._next_surface_id += 1
        
        # Send wl_compositor.create_surface request
        self.send_request(self._wl_compositor, 1, surface_id)
        
        return surface_id
    
    def create_shm_pool(self, size: int) -> int:
        """Create a shared memory pool.
        
        Returns the pool ID.
        """
        if self._wl_shm is None:
            logger.error("wl_shm not bound")
            return -1
        
        # Create temp file for shared memory
        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.write(b"\x00" * size)
        tmp.flush()
        fd = os.open(tmp.name, os.O_RDWR | os.O_CREAT)
        os.ftruncate(fd, size)
        os.unlink(tmp.name)
        
        pool_id = self._alloc_object_id()
        self._wl_shm_pool = pool_id
        
        # Send wl_shm.create_pool request
        self.send_request(self._wl_shm, 1, pool_id, fd, size)
        
        os.close(tmp.fileno())
        
        return pool_id
    
    def create_buffer(self, pool_id: int, offset: int, width: int,
                     height: int, stride: int, format: int = 0) -> int:
        """Create a buffer in the SHM pool.
        
        Returns the buffer ID.
        """
        buffer_id = self._alloc_object_id()
        
        # Send wl_shm_pool.create_buffer request
        self.send_request(pool_id, 1, buffer_id, offset, width, height, stride, format)
        
        return buffer_id
    
    def attach_buffer(self, surface_id: int, buffer_id: int,
                     x: int = 0, y: int = 0) -> bool:
        """Attach a buffer to a surface.
        
        Returns True on success, False on failure.
        """
        return self.send_request(surface_id, 1, buffer_id, x, y)
    
    def damage_surface(self, surface_id: int,
                      x: int = 0, y: int = 0, w: int = 0, h: int = 0) -> bool:
        """Damage a surface region.
        
        Returns True on success, False on failure.
        """
        return self.send_request(surface_id, 2, x, y, w, h)
    
    def commit_surface(self, surface_id: int) -> bool:
        """Commit a surface.
        
        Returns True on success, False on failure.
        """
        return self.send_request(surface_id, 6)
    
    def destroy_surface(self, surface_id: int) -> bool:
        """Destroy a surface.
        
        Returns True on success, False on failure.
        """
        return self.send_request(surface_id, 0)
    
    def _alloc_object_id(self) -> int:
        """Allocate a new object ID."""
        obj_id = self._next_object_id
        self._next_object_id += 1
        return obj_id
    
    def get_events(self) -> List[WaylandEvent]:
        """Get all received events."""
        with self._lock:
            return self._events.copy()
    
    def clear_events(self):
        """Clear received events."""
        with self._lock:
            self._events.clear()
    
    @property
    def is_connected(self) -> bool:
        """Check if connected to compositor."""
        return self._connected
    
    def get_object_ids(self) -> dict:
        """Get current object IDs."""
        return {
            "wl_display": self._wl_display,
            "wl_registry": self._wl_registry,
            "wl_compositor": self._wl_compositor,
            "wl_shm": self._wl_shm,
            "wl_output": self._wl_output,
            "wl_seat": self._wl_seat,
        }
