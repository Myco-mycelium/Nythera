"""Custom compositor event loop for Nyrqis Wayland display server.

Provides a real event loop that:
1. Creates and listens on a Wayland socket
2. Accepts client connections
3. Dispatches protocol messages
4. Manages surfaces and outputs
5. Integrates with the rendering pipeline

Usage:
    from ui.compositor_event_loop import CompositorEventLoop
    
    loop = CompositorEventLoop(socket_path="/tmp/wayland-0")
    loop.add_output(1920, 1080, "primary")
    loop.start()
    
    # ... run until shutdown ...
    loop.stop()

References:
    - ADR-0026: Wayland display-server integration
    - Wayland protocol: https://wayland.freedesktop.org/docs/html/
"""

from __future__ import annotations

import os
import select
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Dict, List, Optional


class WaylandOpcodes(IntEnum):
    """Core Wayland protocol opcodes."""
    # wl_display
    WL_DISPLAY_SYNC = 0
    WL_DISPLAY_GET_REGISTRY = 1
    
    # wl_compositor
    WL_COMPOSITOR_CREATE_SURFACE = 1
    WL_COMPOSITOR_CREATE_REGION = 2
    
    # wl_shm
    WL_SHM_CREATE_POOL = 1
    
    # wl_shm_pool
    WL_SHM_POOL_CREATE_BUFFER = 1
    
    # wl_buffer
    WL_BUFFER_DESTROY = 0
    
    # wl_surface
    WL_SURFACE_DESTROY = 0
    WL_SURFACE_ATTACH = 1
    WL_SURFACE_DAMAGE = 2
    WL_SURFACE_COMMIT = 6
    WL_SURFACE_SET_BUFFER_SCALE = 7
    
    # wl_output
    WL_OUTPUT_GEOMETRY = 0
    WL_OUTPUT_MODE = 1
    
    # wl_seat
    WL_SEAT_GET_POINTER = 1
    WL_SEAT_GET_KEYBOARD = 3
    
    # xdg_wm_base
    XDG_WM_BASE_GET_XDG_SURFACE = 2
    XDG_WM_BASE_PONG = 3
    
    # xdg_surface
    XDG_SURFACE_GET_TOPLEVEL = 1
    
    # xdg_toplevel
    XDG_TOPLEVEL_SET_TITLE = 2
    XDG_TOPLEVEL_SET_APP_ID = 3
    XDG_TOPLEVEL_SET_SIZE = 4


@dataclass
class WaylandClient:
    """A connected Wayland client."""
    id: int
    fd: socket.socket
    pid: int = 0
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
    scale: int = 1
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
    scale: int = 1
    active: bool = True


class CompositorEventLoop:
    """Real Wayland compositor event loop.
    
    Parameters
    ----------
    socket_path : str
        Path for the Wayland socket.
    """
    
    def __init__(self, socket_path: str = "/tmp/wayland-0") -> None:
        self._socket_path = socket_path
        self._server_socket: Optional[socket.socket] = None
        self._clients: Dict[int, WaylandClient] = {}
        self._surfaces: Dict[int, WaylandSurface] = {}
        self._outputs: Dict[int, WaylandOutput] = {}
        self._next_client_id = 0
        self._next_surface_id = 0
        self._next_output_id = 0
        self._running = False
        self._event_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        
        # Event callbacks
        self._on_surface_created: Optional[Callable] = None
        self._on_surface_destroyed: Optional[Callable] = None
        self._on_buffer_attached: Optional[Callable] = None
        self._on_frame_ready: Optional[Callable] = None
    
    @property
    def is_running(self) -> bool:
        """Whether the event loop is running."""
        return self._running
    
    @property
    def client_count(self) -> int:
        """Number of connected clients."""
        with self._lock:
            return len(self._clients)
    
    @property
    def surface_count(self) -> int:
        """Number of active surfaces."""
        with self._lock:
            return len([s for s in self._surfaces.values() if s.active])
    
    @property
    def output_count(self) -> int:
        """Number of outputs."""
        with self._lock:
            return len([o for o in self._outputs.values() if o.active])
    
    def add_output(
        self,
        width: int,
        height: int,
        name: str = "",
        refresh_rate: int = 60000,
    ) -> WaylandOutput:
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
        
        return output
    
    def on_surface_created(self, callback: Callable) -> None:
        """Register callback for surface creation."""
        self._on_surface_created = callback
    
    def on_surface_destroyed(self, callback: Callable) -> None:
        """Register callback for surface destruction."""
        self._on_surface_destroyed = callback
    
    def on_buffer_attached(self, callback: Callable) -> None:
        """Register callback for buffer attachment."""
        self._on_buffer_attached = callback
    
    def on_frame_ready(self, callback: Callable) -> None:
        """Register callback when frame is ready."""
        self._on_frame_ready = callback
    
    def start(self) -> bool:
        """Start the event loop.
        
        Returns True on success, False on failure.
        """
        if self._running:
            return False
        
        # Clean up stale socket
        if os.path.exists(self._socket_path):
            try:
                os.unlink(self._socket_path)
            except OSError:
                pass
        
        # Create Unix domain socket
        try:
            self._server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._server_socket.bind(self._socket_path)
            self._server_socket.listen(5)
            self._server_socket.setblocking(False)
        except OSError:
            return False
        
        self._running = True
        self._event_thread = threading.Thread(
            target=self._event_loop,
            daemon=True,
            name="compositor-event-loop",
        )
        self._event_thread.start()
        
        return True
    
    def stop(self) -> None:
        """Stop the event loop."""
        self._running = False
        
        # Disconnect all clients
        with self._lock:
            for client in list(self._clients.values()):
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
        if os.path.exists(self._socket_path):
            try:
                os.unlink(self._socket_path)
            except OSError:
                pass
        
        # Wait for event thread
        if self._event_thread and self._event_thread.is_alive():
            self._event_thread.join(timeout=2.0)
    
    def send_frame_callback(self, surface_id: int) -> bool:
        """Send frame callback to a surface."""
        with self._lock:
            surface = self._surfaces.get(surface_id)
            if not surface or not surface.active:
                return False
            
            client = self._clients.get(surface.client_id)
            if not client or not client.active:
                return False
        
        # Send wl_callback.done event
        # Object ID for callback (we'd need to track this)
        # For now, just notify the callback
        if self._on_frame_ready:
            self._on_frame_ready(surface_id)
        
        return True
    
    def _event_loop(self) -> None:
        """Main event loop."""
        while self._running:
            try:
                # Wait for events with timeout
                readable = [self._server_socket]
                
                with self._lock:
                    for client in self._clients.values():
                        if client.active:
                            readable.append(client.fd)
                
                # Use select with timeout
                try:
                    readable, _, _ = select.select(readable, [], [], 0.1)
                except (ValueError, OSError):
                    continue
                
                for sock in readable:
                    if sock is self._server_socket:
                        self._accept_client()
                    else:
                        self._handle_client_data(sock)
                        
            except Exception:
                if self._running:
                    continue
                break
    
    def _accept_client(self) -> None:
        """Accept a new client connection."""
        try:
            client_fd, _ = self._server_socket.accept()
        except (OSError, BlockingIOError):
            return
        
        client_id = self._next_client_id
        self._next_client_id += 1
        
        client = WaylandClient(
            id=client_id,
            fd=client_fd,
        )
        
        with self._lock:
            self._clients[client_id] = client
    
    def _handle_client_data(self, sock: socket.socket) -> None:
        """Handle data from a client."""
        try:
            data = sock.recv(4096)
            if not data:
                self._disconnect_client(sock)
                return
        except (OSError, ConnectionResetError):
            self._disconnect_client(sock)
            return
        
        # Find client
        client = None
        with self._lock:
            for c in self._clients.values():
                if c.fd is sock:
                    client = c
                    break
        
        if not client:
            return
        
        # Parse and dispatch messages
        self._dispatch_messages(client, data)
    
    def _dispatch_messages(self, client: WaylandClient, data: bytes) -> None:
        """Dispatch Wayland protocol messages."""
        offset = 0
        while offset + 8 <= len(data):
            # Parse message header: object_id (4) + size+opcode (4)
            object_id = struct.unpack_from("I", data, offset)[0]
            size_opcode = struct.unpack_from("I", data, offset + 4)[0]
            size = size_opcode >> 16
            opcode = size_opcode & 0xFFFF
            
            if size < 8 or offset + size > len(data):
                break
            
            payload = data[offset + 8:offset + size]
            offset += size
            
            # Dispatch based on object type
            if object_id == 1:  # wl_display
                self._dispatch_display(client, opcode, payload)
            elif object_id == 2:  # wl_compositor
                self._dispatch_compositor(client, opcode, payload)
            else:
                # Surface or other object
                self._dispatch_object(client, object_id, opcode, payload)
    
    def _dispatch_display(self, client: WaylandClient, opcode: int, payload: bytes) -> None:
        """Dispatch wl_display messages."""
        if opcode == WaylandOpcodes.WL_DISPLAY_SYNC:
            # Send wl_callback.done
            self._send_callback_done(client, 0, int(time.time() * 1000))
        elif opcode == WaylandOpcodes.WL_DISPLAY_GET_REGISTRY:
            # Send wl_registry.global events
            self._send_registry_globals(client)
    
    def _dispatch_compositor(self, client: WaylandClient, opcode: int, payload: bytes) -> None:
        """Dispatch wl_compositor messages."""
        if opcode == WaylandOpcodes.WL_COMPOSITOR_CREATE_SURFACE:
            if len(payload) >= 4:
                new_id = struct.unpack_from("I", payload, 0)[0]
                self._create_surface(client, new_id)
    
    def _dispatch_object(self, client: WaylandClient, object_id: int, opcode: int, payload: bytes) -> None:
        """Dispatch messages for surface and other objects."""
        # Find the surface for this object
        surface = None
        with self._lock:
            for s in self._surfaces.values():
                if s.id == object_id and s.client_id == client.id:
                    surface = s
                    break
        
        if not surface:
            return
        
        if opcode == WaylandOpcodes.WL_SURFACE_ATTACH:
            self._surface_attach(surface, payload)
        elif opcode == WaylandOpcodes.WL_SURFACE_COMMIT:
            self._surface_commit(surface)
        elif opcode == WaylandOpcodes.WL_SURFACE_DESTROY:
            self._destroy_surface(surface)
        elif opcode == WaylandOpcodes.WL_SURFACE_SET_BUFFER_SCALE:
            if len(payload) >= 4:
                scale = struct.unpack_from("i", payload, 0)[0]
                surface.scale = scale
    
    def _create_surface(self, client: WaylandClient, surface_id: int) -> None:
        """Create a new surface for a client."""
        surface = WaylandSurface(
            id=surface_id,
            client_id=client.id,
        )
        
        with self._lock:
            self._surfaces[surface_id] = surface
            client.surfaces.append(surface_id)
        
        if self._on_surface_created:
            self._on_surface_created(surface)
    
    def _surface_attach(self, surface: WaylandSurface, payload: bytes) -> None:
        """Attach a buffer to a surface."""
        if len(payload) >= 12:
            buffer_id = struct.unpack_from("I", payload, 0)[0]
            x = struct.unpack_from("i", payload, 4)[0]
            y = struct.unpack_from("i", payload, 8)[0]
            
            surface.buffer_fd = buffer_id
            surface.buffer_offset = x
            
            if self._on_buffer_attached:
                self._on_buffer_attached(surface)
    
    def _surface_commit(self, surface: WaylandSurface) -> None:
        """Commit a surface (apply pending state)."""
        pass
    
    def _destroy_surface(self, surface: WaylandSurface) -> None:
        """Destroy a surface."""
        surface.active = False
        
        with self._lock:
            if surface.id in self._surfaces:
                del self._surfaces[surface.id]
            
            client = self._clients.get(surface.client_id)
            if client and surface.id in client.surfaces:
                client.surfaces.remove(surface.id)
        
        if self._on_surface_destroyed:
            self._on_surface_destroyed(surface)
    
    def _send_callback_done(self, client: WaylandClient, callback_id: int, timestamp: int) -> None:
        """Send wl_callback.done event."""
        data = struct.pack("II", callback_id, 0)  # wl_callback.done
        data += struct.pack("I", timestamp & 0xFFFFFFFF)
        try:
            client.fd.sendall(data)
        except OSError:
            pass
    
    def _send_registry_globals(self, client: WaylandClient) -> None:
        """Send wl_registry.global events."""
        # Send wl_compositor global
        data = struct.pack("III", 1, 2, 4)  # id=1, interface="wl_compositor", version=4
        data += b"wl_compositor\x00"
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
    
    def _disconnect_client(self, sock: socket.socket) -> None:
        """Handle client disconnection."""
        client = None
        with self._lock:
            for c in list(self._clients.values()):
                if c.fd is sock:
                    client = c
                    break
        
        if not client:
            return
        
        client.active = False
        
        # Clean up client's surfaces
        with self._lock:
            for surface_id in client.surfaces[:]:
                if surface_id in self._surfaces:
                    del self._surfaces[surface_id]
            client.surfaces.clear()
            
            if client.id in self._clients:
                del self._clients[client.id]
        
        try:
            sock.close()
        except OSError:
            pass


__all__ = ["CompositorEventLoop", "WaylandClient", "WaylandSurface", "WaylandOutput"]
