"""wayland_socket — Wayland compositor socket for client connections.

Provides a Unix domain socket server that Wayland clients can connect to.
This is the foundation for real Wayland display server functionality.

The socket handles:
1. Client connection management
2. Wayland protocol message dispatching
3. Surface lifecycle management
4. Buffer sharing via shared memory

References:
    - ADR-0026: Wayland display-server integration
    - Wayland protocol: https://wayland.freedesktop.org/docs/html/
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import socket
import struct
import tempfile
import threading
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# Wayland protocol message opcodes (subset)
class WaylandOpcodes(IntEnum):
    """Core Wayland protocol opcodes."""
    WL_DISPLAY_GET_REGISTRY = 1
    WL_DISPLAY_SYNC = 0
    WL_COMPOSITOR_CREATE_SURFACE = 1
    WL_COMPOSITOR_CREATE_REGION = 2
    WL_SHM_CREATE_POOL = 1
    WL_SHM_POOL_CREATE_BUFFER = 1
    WL_BUFFER_DESTROY = 0
    WL_SURFACE_ATTACH = 1
    WL_SURFACE_DAMAGE = 2
    WL_SURFACE_COMMIT = 6
    WL_SURFACE_DESTROY = 0
    WL_OUTPUT_GEOMETRY = 0
    WL_OUTPUT_MODE = 1
    WL_SEAT_GET_POINTER = 1
    WL_SEAT_GET_KEYBOARD = 2
    WL_POINTER_MOTION = 0
    WL_POINTER_BUTTON = 1
    WL_KEYBOARD_KEY = 1
    WL_KEYBOARD_MODIFIERS = 4
    XDG_WM_BASE_GET_XDG_SURFACE = 2
    XDG_WM_BASE_PONG = 3
    XDG_SURFACE_GET_TOPLEVEL = 1
    XDG_TOPLEVEL_SET_TITLE = 2
    XDG_TOPLEVEL_SET_APP_ID = 3
    XDG_TOPLEVEL_SET_SIZE = 4
    XDG_TOPLEVEL_SET_MIN_SIZE = 5
    XDG_TOPLEVEL_SET_MAX_SIZE = 6


@dataclass
class WaylandClient:
    """A connected Wayland client."""
    id: int
    fd: socket.socket
    pid: int
    surfaces: List[int] = field(default_factory=list)
    connected_at: float = field(default_factory=time.time)
    active: bool = True


@dataclass
class WaylandSurface:
    """A Wayland surface."""
    id: int
    client_id: int
    width: int = 0
    height: int = 0
    buffer_fd: int = -1
    buffer_offset: int = 0
    buffer_stride: int = 0
    active: bool = True


@dataclass
class WaylandOutput:
    """A Wayland output."""
    id: int
    name: str
    width: int
    height: int
    refresh_rate: int = 60000
    x: int = 0
    y: int = 0
    active: bool = True


class WaylandSocketServer:
    """Wayland compositor socket server.
    
    Manages client connections and dispatches Wayland protocol messages.
    
    Usage:
        server = WaylandSocketServer("/tmp/wayland-0")
        server.start()
        # ... server runs in background ...
        server.stop()
    """
    
    def __init__(self, socket_path: str = "/tmp/wayland-0"):
        self.socket_path = socket_path
        self._server_socket: Optional[socket.socket] = None
        self._clients: Dict[int, WaylandClient] = {}
        self._surfaces: Dict[int, WaylandSurface] = {}
        self._outputs: Dict[int, WaylandOutput] = {}
        self._next_client_id = 0
        self._next_surface_id = 0
        self._next_output_id = 0
        self._running = False
        self._server_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        
        # Event handlers
        self._on_surface_created: Optional[Callable] = None
        self._on_surface_destroyed: Optional[Callable] = None
        self._on_buffer_attached: Optional[Callable] = None
    
    def set_surface_callback(self, callback: Callable):
        """Set callback for surface creation events."""
        self._on_surface_created = callback
    
    def set_buffer_callback(self, callback: Callable):
        """Set callback for buffer attachment events."""
        self._on_buffer_attached = callback
    
    def start(self) -> bool:
        """Start the socket server.
        
        Returns True on success, False on failure.
        """
        if self._running:
            logger.warning("Socket server already running")
            return False
        
        # Clean up stale socket
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass
        
        # Create Unix domain socket
        try:
            self._server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._server_socket.bind(self.socket_path)
            self._server_socket.listen(5)
            self._server_socket.settimeout(0.1)  # Non-blocking for shutdown
        except OSError as exc:
            logger.error("Failed to create socket: %s", exc)
            return False
        
        self._running = True
        self._server_thread = threading.Thread(
            target=self._accept_loop,
            daemon=True,
            name="wayland-socket",
        )
        self._server_thread.start()
        
        logger.info("Wayland socket server started: %s", self.socket_path)
        return True
    
    def stop(self):
        """Stop the socket server and disconnect all clients."""
        self._running = False
        
        # Disconnect all clients
        with self._lock:
            for client in self._clients.values():
                try:
                    client.fd.close()
                except OSError:
                    pass
            self._clients.clear()
        
        # Close server socket
        if self._server_socket:
            try:
                self._server_socket.close()
            except OSError:
                pass
            self._server_socket = None
        
        # Clean up socket file
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self._socket_path)
            except OSError:
                pass
        
        # Wait for server thread
        if self._server_thread and self._server_thread.is_alive():
            self._server_thread.join(timeout=2.0)
        
        logger.info("Wayland socket server stopped")
    
    def _accept_loop(self):
        """Accept new client connections."""
        while self._running:
            try:
                client_fd, addr = self._server_socket.accept()
                self._handle_new_client(client_fd)
            except socket.timeout:
                continue
            except OSError:
                if self._running:
                    logger.error("Error accepting connection")
                break
    
    def _handle_new_client(self, fd: socket.socket):
        """Handle a new client connection."""
        client_id = self._next_client_id
        self._next_client_id += 1
        
        # Get client PID (if available)
        pid = 0
        try:
            # SO_PEERCRED not directly available in Python, but we can try
            pass
        except Exception:
            pass
        
        client = WaylandClient(
            id=client_id,
            fd=fd,
            pid=pid,
        )
        
        with self._lock:
            self._clients[client_id] = client
        
        logger.info("Client connected: id=%d, pid=%d", client_id, pid)
        
        # Start reading from client
        threading.Thread(
            target=self._read_client_loop,
            args=(client,),
            daemon=True,
            name=f"wayland-client-{client_id}",
        ).start()
    
    def _read_client_loop(self, client: WaylandClient):
        """Read and dispatch messages from a client."""
        while self._running and client.active:
            try:
                data = client.fd.recv(4096)
                if not data:
                    break
                self._dispatch_message(client, data)
            except (ConnectionResetError, BrokenPipeError):
                break
            except OSError:
                if self._running and client.active:
                    logger.warning("Error reading from client %d", client.id)
                break
        
        # Client disconnected
        self._disconnect_client(client)
    
    def _dispatch_message(self, client: WaylandClient, data: bytes):
        """Dispatch a Wayland protocol message."""
        # Parse message header: object_id (4) + size+opcode (4)
        if len(data) < 8:
            return
        
        object_id = struct.unpack("I", data[0:4])[0]
        size_opcode = struct.unpack("I", data[4:8])[0]
        size = size_opcode >> 16
        opcode = size_opcode & 0xFFFF
        
        # Extract message payload
        payload = data[8:size] if size > 8 else b""
        
        # Dispatch based on object type
        if object_id == 1:  # wl_display
            self._dispatch_display(client, opcode, payload)
        elif object_id == 2:  # wl_compositor
            self._dispatch_compositor(client, opcode, payload)
        elif object_id == 3:  # wl_shm
            self._dispatch_shm(client, opcode, payload)
        else:
            # Surface or other object
            self._dispatch_object(client, object_id, opcode, payload)
    
    def _dispatch_display(self, client: WaylandClient, opcode: int, payload: bytes):
        """Dispatch wl_display messages."""
        if opcode == WaylandOpcodes.WL_DISPLAY_SYNC:
            # Send wl_callback.done
            self._send_callback_done(client, 0, int(time.time() * 1000))
        elif opcode == WaylandOpcodes.WL_DISPLAY_GET_REGISTRY:
            # Send wl_registry.global events
            self._send_registry_globals(client)
    
    def _dispatch_compositor(self, client: WaylandClient, opcode: int, payload: bytes):
        """Dispatch wl_compositor messages."""
        if opcode == WaylandOpcodes.WL_COMPOSITOR_CREATE_SURFACE:
            # Create a new surface
            if len(payload) >= 4:
                new_id = struct.unpack("I", payload[0:4])[0]
                self._create_surface(client, new_id)
    
    def _dispatch_shm(self, client: WaylandClient, opcode: int, payload: bytes):
        """Dispatch wl_shm messages."""
        pass  # SHM handling deferred to buffer attachment
    
    def _dispatch_object(self, client: WaylandClient, object_id: int,
                         opcode: int, payload: bytes):
        """Dispatch messages for surface and other objects."""
        # Find the surface for this object
        surface = None
        with self._lock:
            for s in self._surfaces.values():
                if s.id == object_id and s.client_id == client.id:
                    surface = s
                    break
        
        if surface is None:
            return
        
        if opcode == WaylandOpcodes.WL_SURFACE_ATTACH:
            self._surface_attach(surface, payload)
        elif opcode == WaylandOpcodes.WL_SURFACE_DAMAGE:
            pass  # Damage tracking deferred
        elif opcode == WaylandOpcodes.WL_SURFACE_COMMIT:
            self._surface_commit(surface)
        elif opcode == WaylandOpcodes.WL_SURFACE_DESTROY:
            self._destroy_surface(surface)
    
    def _create_surface(self, client: WaylandClient, surface_id: int):
        """Create a new surface for a client."""
        surface = WaylandSurface(
            id=surface_id,
            client_id=client.id,
        )
        
        with self._lock:
            self._surfaces[surface_id] = surface
            client.surfaces.append(surface_id)
        
        logger.debug("Surface created: id=%d, client=%d", surface_id, client.id)
        
        if self._on_surface_created:
            self._on_surface_created(surface)
    
    def _surface_attach(self, surface: WaylandSurface, payload: bytes):
        """Attach a buffer to a surface."""
        if len(payload) >= 16:
            buffer_id = struct.unpack("I", payload[0:4])[0]
            x = struct.unpack("i", payload[4:8])[0]
            y = struct.unpack("i", payload[8:12])[0]
            
            surface.buffer_fd = buffer_id
            surface.buffer_offset = x
            
            logger.debug("Buffer attached to surface %d: buffer=%d",
                        surface.id, buffer_id)
            
            if self._on_buffer_attached:
                self._on_buffer_attached(surface)
    
    def _surface_commit(self, surface: WaylandSurface):
        """Commit a surface (apply pending state)."""
        logger.debug("Surface committed: id=%d", surface.id)
    
    def _destroy_surface(self, surface: WaylandSurface):
        """Destroy a surface."""
        surface.active = False
        
        with self._lock:
            if surface.id in self._surfaces:
                del self._surfaces[surface.id]
            
            # Remove from client's surface list
            client = self._clients.get(surface.client_id)
            if client and surface.id in client.surfaces:
                client.surfaces.remove(surface.id)
        
        logger.debug("Surface destroyed: id=%d", surface.id)
        
        if self._on_surface_destroyed:
            self._on_surface_destroyed(surface)
    
    def _send_callback_done(self, client: WaylandClient, callback_id: int,
                           timestamp: int):
        """Send wl_callback.done event."""
        data = struct.pack("II", callback_id, 0)  # wl_callback.done
        data += struct.pack("I", timestamp)
        try:
            client.fd.sendall(data)
        except OSError:
            pass
    
    def _send_registry_globals(self, client: WaylandClient):
        """Send wl_registry.global events."""
        # Send wl_compositor global
        data = struct.pack("III", 1, 2, 4)  # id=1, interface="wl_compositor", version=4
        data += b"wl_compositor\x00"
        # Pad to 4-byte alignment
        while len(data) % 4:
            data += b"\x00"
        
        # Send wl_shm global
        data += struct.pack("III", 2, 3, 1)  # id=2, interface="wl_shm", version=1
        data += b"wl_shm\x00"
        while len(data) % 4:
            data += b"\x00"
        
        # Send wl_output global
        data += struct.pack("III", 3, 2, 3)  # id=3, interface="wl_output", version=3
        data += b"wl_output\x00"
        while len(data) % 4:
            data += b"\x00"
        
        try:
            client.fd.sendall(data)
        except OSError:
            pass
    
    def _disconnect_client(self, client: WaylandClient):
        """Handle client disconnection."""
        client.active = False
        
        # Clean up client's surfaces
        with self._lock:
            for surface_id in client.surfaces[:]:
                if surface_id in self._surfaces:
                    del self._surfaces[surface_id]
            client.surfaces.clear()
            
            # Remove client
            if client.id in self._clients:
                del self._clients[client.id]
        
        try:
            client.fd.close()
        except OSError:
            pass
        
        logger.info("Client disconnected: id=%d", client.id)
    
    def add_output(self, width: int, height: int, name: str = "",
                   refresh_rate: int = 60000) -> WaylandOutput:
        """Add a display output."""
        output_id = self._next_output_id
        self._next_output_id += 1
        
        output = WaylandOutput(
            id=output_id,
            name=name or f"output-{output_id}",
            width=width,
            height=height,
            refresh_rate=refresh_rate,
        )
        
        with self._lock:
            self._outputs[output_id] = output
        
        logger.info("Output added: %dx%d@%dmHz", width, height, refresh_rate)
        return output
    
    def get_client_count(self) -> int:
        """Get the number of connected clients."""
        with self._lock:
            return len(self._clients)
    
    def get_surface_count(self) -> int:
        """Get the number of active surfaces."""
        with self._lock:
            return len(self._surfaces)
    
    def get_output_count(self) -> int:
        """Get the number of outputs."""
        with self._lock:
            return len(self._outputs)
    
    @property
    def is_running(self) -> bool:
        """Check if the server is running."""
        return self._running
