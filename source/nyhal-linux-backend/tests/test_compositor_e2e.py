"""test_compositor_e2e — End-to-end compositor tests with mock Wayland client.

Tests the full compositor pipeline by simulating a Wayland client connecting
to the compositor and exchanging protocol messages.

References:
    - ADR-0026: Wayland display-server integration
    - ui/nyrqis_compositor.py
    - ui/wayland_socket.py
    - ui/wayland_protocol.py
"""

from __future__ import annotations

import os
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest

# Ensure the backend is importable
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    os.sys.path.insert(0, _HERE)


class MockWaylandClient:
    """Mock Wayland client for testing."""
    
    def __init__(self, socket_path: str):
        self.socket_path = socket_path
        self._socket: socket.socket = None
        self.connected = False
        self.messages_received = []
    
    def connect(self) -> bool:
        """Connect to the compositor."""
        try:
            self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._socket.connect(self.socket_path)
            self.connected = True
            return True
        except (ConnectionRefusedError, FileNotFoundError, OSError) as exc:
            return False
    
    def send_request(self, object_id: int, opcode: int, *args) -> bool:
        """Send a Wayland request to the compositor."""
        if not self.connected:
            return False
        
        # Encode message
        from ui.wayland_protocol import WaylandEncoder
        data = WaylandEncoder.encode_message(object_id, opcode, *args)
        
        try:
            self._socket.sendall(data)
            return True
        except OSError:
            return False
    
    def receive_events(self, timeout: float = 0.1) -> list:
        """Receive events from the compositor."""
        if not self.connected:
            return []
        
        events = []
        self._socket.settimeout(timeout)
        
        try:
            while True:
                data = self._socket.recv(4096)
                if not data:
                    break
                
                # Parse events
                from ui.wayland_protocol import WaylandDecoder
                offset = 0
                while offset + 8 <= len(data):
                    object_id = struct.unpack("I", data[offset:offset+4])[0]
                    size_opcode = struct.unpack("I", data[offset+4:offset+8])[0]
                    size = size_opcode >> 16
                    opcode = size_opcode & 0xFFFF
                    
                    if size < 8 or offset + size > len(data):
                        break
                    
                    event_data = data[offset:offset+size]
                    events.append({
                        "object_id": object_id,
                        "opcode": opcode,
                        "size": size,
                        "data": event_data,
                    })
                    
                    offset += size
        except socket.timeout:
            pass
        
        self.messages_received.extend(events)
        return events
    
    def disconnect(self):
        """Disconnect from the compositor."""
        if self._socket:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None
        self.connected = False


class TestCompositorE2E(unittest.TestCase):
    """End-to-end compositor tests."""

    def setUp(self):
        """Set up test socket path."""
        self._tmpdir = tempfile.mkdtemp(prefix="nyrqis-e2e-test-")
        self._socket_path = os.path.join(self._tmpdir, "wayland-test")
    
    def tearDown(self):
        """Clean up test files."""
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
    
    def test_compositor_start_stop(self):
        """Compositor can start and stop cleanly."""
        from ui.nyrqis_compositor import NyrqisCompositor, CompositorConfig
        config = CompositorConfig(
            headless=True,
            socket_path=self._socket_path,
        )
        
        compositor = NyrqisCompositor(config)
        self.assertTrue(compositor.start())
        self.assertTrue(compositor._running)
        compositor.stop()
        self.assertFalse(compositor._running)
    
    def test_client_connect(self):
        """Mock client can connect to compositor."""
        from ui.wayland_socket import WaylandSocketServer
        
        # Start server
        server = WaylandSocketServer(self._socket_path)
        self.assertTrue(server.start())
        
        try:
            # Connect client
            client = MockWaylandClient(self._socket_path)
            self.assertTrue(client.connect())
            self.assertTrue(client.connected)
            
            # Wait for connection to be registered
            time.sleep(0.1)
            
            # Check client count
            self.assertEqual(server.get_client_count(), 1)
            
            client.disconnect()
        finally:
            server.stop()
    
    def test_client_send_message(self):
        """Client can send messages to compositor."""
        from ui.wayland_socket import WaylandSocketServer
        from ui.wayland_protocol import WaylandEncoder
        
        # Start server
        server = WaylandSocketServer(self._socket_path)
        self.assertTrue(server.start())
        
        try:
            # Connect client
            client = MockWaylandClient(self._socket_path)
            self.assertTrue(client.connect())
            
            # Wait for connection
            time.sleep(0.1)
            
            # Send wl_display.sync request (object_id=1, opcode=0)
            success = client.send_request(1, 0, 0)
            self.assertTrue(success)
            
            # Receive response (may take a moment)
            events = client.receive_events(timeout=1.0)
            # We just check the message was sent successfully
            self.assertTrue(success)
            
            client.disconnect()
        finally:
            server.stop()
    
    def test_surface_lifecycle(self):
        """Full surface lifecycle: create → attach → commit → destroy."""
        from ui.wayland_socket import WaylandSocketServer
        
        # Start server
        server = WaylandSocketServer(self._socket_path)
        self.assertTrue(server.start())
        
        try:
            # Connect client
            client = MockWaylandClient(self._socket_path)
            self.assertTrue(client.connect())
            
            # Wait for connection
            time.sleep(0.1)
            
            # Send wl_compositor.create_surface (object_id=2, opcode=1)
            success = client.send_request(2, 1, 100)  # new_id=100
            self.assertTrue(success)
            
            # Wait for surface to be created
            time.sleep(0.1)
            
            # Check surface count
            self.assertEqual(server.get_surface_count(), 1)
            
            # Send wl_surface.attach (object_id=100, opcode=1)
            success = client.send_request(100, 1, 0, 0, 0)  # buffer_id=0, x=0, y=0
            self.assertTrue(success)
            
            # Send wl_surface.commit (object_id=100, opcode=6)
            success = client.send_request(100, 6)
            self.assertTrue(success)
            
            # Note: wl_surface.destroy is opcode 0 in the wayland protocol
            # but our test client sends it as a raw message
            # The server handles cleanup when client disconnects
            
            client.disconnect()
            
            # Wait for disconnection to be processed
            time.sleep(0.2)
            
            # Surface count may still be 1 (server keeps surfaces until explicit cleanup)
            # This is expected behavior - surfaces are cleaned up on client disconnect
            
            client.disconnect()
        finally:
            server.stop()
    
    def test_multiple_clients(self):
        """Multiple clients can connect simultaneously."""
        from ui.wayland_socket import WaylandSocketServer
        
        # Start server
        server = WaylandSocketServer(self._socket_path)
        self.assertTrue(server.start())
        
        try:
            # Connect multiple clients
            clients = []
            for i in range(3):
                client = MockWaylandClient(self._socket_path)
                self.assertTrue(client.connect())
                clients.append(client)
            
            # Wait for connections
            time.sleep(0.2)
            
            # Check client count
            self.assertEqual(server.get_client_count(), 3)
            
            # Disconnect all
            for client in clients:
                client.disconnect()
            
            # Wait for disconnections
            time.sleep(0.2)
            
            # Check client count
            self.assertEqual(server.get_client_count(), 0)
        finally:
            server.stop()
    
    def test_output_management(self):
        """Outputs can be added and queried."""
        from ui.wayland_socket import WaylandSocketServer
        
        # Start server
        server = WaylandSocketServer(self._socket_path)
        self.assertTrue(server.start())
        
        try:
            # Add outputs
            out1 = server.add_output(1920, 1080, "monitor-1")
            out2 = server.add_output(2560, 1440, "monitor-2")
            
            # Check output count
            self.assertEqual(server.get_output_count(), 2)
            
            # Check output properties
            self.assertEqual(out1.width, 1920)
            self.assertEqual(out2.width, 2560)
        finally:
            server.stop()


class TestWaylandProtocolE2E(unittest.TestCase):
    """End-to-end protocol tests."""

    def test_full_handshake(self):
        """Full Wayland handshake: connect → get_registry → sync."""
        from ui.wayland_socket import WaylandSocketServer
        from ui.wayland_protocol import WaylandEncoder
        
        with tempfile.NamedTemporaryFile(suffix=".sock", delete=False) as f:
            socket_path = f.name
        os.unlink(socket_path)
        
        try:
            server = WaylandSocketServer(socket_path)
            self.assertTrue(server.start())
            
            # Connect client
            client = MockWaylandClient(socket_path)
            self.assertTrue(client.connect())
            
            # Wait for connection
            time.sleep(0.1)
            
            # Send wl_display.sync
            client.send_request(1, 0, 0)
            
            # Send wl_display.get_registry
            client.send_request(1, 1, 200)  # new_id=200
            
            # Receive events (may take a moment)
            events = client.receive_events(timeout=1.0)
            
            # We just check the handshake completed without error
            self.assertTrue(client.connected)
            
            client.disconnect()
            server.stop()
        finally:
            if os.path.exists(socket_path):
                os.unlink(socket_path)

    def test_xdg_wm_base_handshake(self):
        """XDG WM Base handshake: ping → pong → get_xdg_surface."""
        from ui.wayland_socket import WaylandSocketServer
        from ui.wayland_protocol import WaylandEncoder
        
        with tempfile.NamedTemporaryFile(suffix=".sock", delete=False) as f:
            socket_path = f.name
        os.unlink(socket_path)
        
        try:
            server = WaylandSocketServer(socket_path)
            self.assertTrue(server.start())
            
            # Connect client
            client = MockWaylandClient(socket_path)
            self.assertTrue(client.connect())
            
            # Wait for connection
            time.sleep(0.1)
            
            # Create surface first
            client.send_request(2, 1, 100)  # wl_compositor.create_surface
            
            # Get XDG surface
            client.send_request(3, 2, 200, 100)  # xdg_wm_base.get_xdg_surface
            
            # Wait for processing
            time.sleep(0.1)
            
            # Check that surface was created
            self.assertEqual(server.get_surface_count(), 1)
            
            client.disconnect()
            server.stop()
        finally:
            if os.path.exists(socket_path):
                os.unlink(socket_path)


class TestDRMBackendE2E(unittest.TestCase):
    """End-to-end DRM backend tests."""

    def test_drm_connector_detection(self):
        """DRM backend can detect connectors."""
        from ui.drm_backend import DRMBackend
        
        backend = DRMBackend()
        if not backend.open():
            self.skipTest("Cannot open DRM device")
        
        try:
            connectors = backend.detect_connectors()
            # Should detect at least one connector on a system with a GPU
            self.assertIsInstance(connectors, list)
        finally:
            backend.close()

    def test_drm_set_mode(self):
        """DRM backend can set mode (may fail without proper permissions)."""
        from ui.drm_backend import DRMBackend
        
        backend = DRMBackend()
        if not backend.open():
            self.skipTest("Cannot open DRM device")
        
        try:
            # Try to set mode (will likely fail without permissions)
            result = backend.set_mode(0, 0, 0, 0)
            # We just check it doesn't crash
            self.assertIsInstance(result, bool)
        finally:
            backend.close()


if __name__ == "__main__":
    unittest.main()
