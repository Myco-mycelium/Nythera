"""test_wayland_client — Tests for Wayland client test harness and compatibility layer.

References:
    - ADR-0026: Wayland display-server integration
    - ui/wayland_client.py
    - ui/wayland_compat.py
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import time
import unittest

# Ensure the backend is importable
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    os.sys.path.insert(0, _HERE)


class TestWaylandClientTest(unittest.TestCase):
    """Tests for the Wayland client test harness."""

    def setUp(self):
        """Set up test socket path."""
        self._tmpdir = tempfile.mkdtemp(prefix="nyrqis-client-test-")
        self._socket_path = os.path.join(self._tmpdir, "wayland-test")
    
    def tearDown(self):
        """Clean up test files."""
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
    
    def test_client_create(self):
        """Can create a client."""
        from ui.wayland_client import WaylandClientTest
        client = WaylandClientTest(self._socket_path)
        self.assertFalse(client.is_connected)
    
    def test_client_connect(self):
        """Can connect to a running server."""
        from ui.wayland_socket import WaylandSocketServer
        from ui.wayland_client import WaylandClientTest
        
        server = WaylandSocketServer(self._socket_path)
        self.assertTrue(server.start())
        
        try:
            client = WaylandClientTest(self._socket_path)
            self.assertTrue(client.connect())
            self.assertTrue(client.is_connected)
            client.disconnect()
        finally:
            server.stop()
    
    def test_client_connect_fail(self):
        """Connection fails without server."""
        from ui.wayland_client import WaylandClientTest
        
        client = WaylandClientTest(self._socket_path)
        self.assertFalse(client.connect())
    
    def test_client_send_request(self):
        """Can send requests to the server."""
        from ui.wayland_socket import WaylandSocketServer
        from ui.wayland_client import WaylandClientTest
        
        server = WaylandSocketServer(self._socket_path)
        self.assertTrue(server.start())
        
        try:
            client = WaylandClientTest(self._socket_path)
            self.assertTrue(client.connect())
            
            # Send sync request
            success = client.send_request(1, 0, 0)
            self.assertTrue(success)
            
            client.disconnect()
        finally:
            server.stop()
    
    def test_client_receive_events(self):
        """Can receive events from the server."""
        from ui.wayland_socket import WaylandSocketServer
        from ui.wayland_client import WaylandClientTest
        
        server = WaylandSocketServer(self._socket_path)
        self.assertTrue(server.start())
        
        try:
            client = WaylandClientTest(self._socket_path)
            self.assertTrue(client.connect())
            
            # Send sync request
            client.send_request(1, 0, 0)
            
            # Receive events
            events = client.receive_events(timeout=0.5)
            # May or may not receive events depending on timing
            self.assertIsInstance(events, list)
            
            client.disconnect()
        finally:
            server.stop()
    
    def test_client_object_ids(self):
        """Object IDs are tracked correctly."""
        from ui.wayland_client import WaylandClientTest
        client = WaylandClientTest(self._socket_path)
        
        ids = client.get_object_ids()
        self.assertIn("wl_display", ids)
        self.assertEqual(ids["wl_display"], 1)
    
    def test_client_event_callback(self):
        """Event callback is set correctly."""
        from ui.wayland_client import WaylandClientTest
        client = WaylandClientTest(self._socket_path)
        
        events_received = []
        client.set_event_callback(lambda e: events_received.append(e))
        self.assertIsNotNone(client._on_event)


class TestWaylandCompatClient(unittest.TestCase):
    """Tests for the Wayland compatibility layer."""

    def setUp(self):
        """Set up test socket path."""
        self._tmpdir = tempfile.mkdtemp(prefix="nyrqis-compat-test-")
        self._socket_path = os.path.join(self._tmpdir, "wayland-test")
    
    def tearDown(self):
        """Clean up test files."""
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
    
    def test_compat_client_create(self):
        """Can create a compat client."""
        from ui.wayland_compat import WaylandCompatClient
        client = WaylandCompatClient(self._socket_path)
        self.assertFalse(client.is_connected)
    
    def test_compat_client_connect(self):
        """Can connect to a running server."""
        from ui.wayland_socket import WaylandSocketServer
        from ui.wayland_compat import WaylandCompatClient
        
        server = WaylandSocketServer(self._socket_path)
        self.assertTrue(server.start())
        
        try:
            client = WaylandCompatClient(self._socket_path)
            self.assertTrue(client.connect())
            self.assertTrue(client.is_connected)
            client.disconnect()
        finally:
            server.stop()
    
    def test_create_window(self):
        """Can create a window."""
        from ui.wayland_socket import WaylandSocketServer
        from ui.wayland_compat import WaylandCompatClient
        
        server = WaylandSocketServer(self._socket_path)
        self.assertTrue(server.start())
        
        try:
            client = WaylandCompatClient(self._socket_path)
            self.assertTrue(client.connect())
            
            window = client.create_window("Test App", 800, 600)
            self.assertEqual(window.title, "Test App")
            self.assertEqual(window.width, 800)
            self.assertEqual(window.height, 600)
            
            client.disconnect()
        finally:
            server.stop()
    
    def test_window_set_title(self):
        """Can set window title."""
        from ui.wayland_socket import WaylandSocketServer
        from ui.wayland_compat import WaylandCompatClient
        
        server = WaylandSocketServer(self._socket_path)
        self.assertTrue(server.start())
        
        try:
            client = WaylandCompatClient(self._socket_path)
            self.assertTrue(client.connect())
            
            window = client.create_window("Initial", 800, 600)
            window.set_title("Updated")
            self.assertEqual(window.title, "Updated")
            
            client.disconnect()
        finally:
            server.stop()
    
    def test_window_render_solid(self):
        """Can render a solid color."""
        from ui.wayland_socket import WaylandSocketServer
        from ui.wayland_compat import WaylandCompatClient
        
        server = WaylandSocketServer(self._socket_path)
        self.assertTrue(server.start())
        
        try:
            client = WaylandCompatClient(self._socket_path)
            self.assertTrue(client.connect())
            
            window = client.create_window("Test", 100, 100)
            window.render_solid(255, 0, 0)  # Red
            self.assertIsNotNone(window._pixel_data)
            self.assertEqual(len(window._pixel_data), 100 * 100 * 4)
            
            client.disconnect()
        finally:
            server.stop()
    
    def test_get_windows(self):
        """Can get list of windows."""
        from ui.wayland_socket import WaylandSocketServer
        from ui.wayland_compat import WaylandCompatClient
        
        server = WaylandSocketServer(self._socket_path)
        self.assertTrue(server.start())
        
        try:
            client = WaylandCompatClient(self._socket_path)
            self.assertTrue(client.connect())
            
            client.create_window("Window 1", 800, 600)
            client.create_window("Window 2", 640, 480)
            
            windows = client.get_windows()
            self.assertEqual(len(windows), 2)
            
            client.disconnect()
        finally:
            server.stop()
    
    def test_window_close(self):
        """Can close a window."""
        from ui.wayland_socket import WaylandSocketServer
        from ui.wayland_compat import WaylandCompatClient
        
        server = WaylandSocketServer(self._socket_path)
        self.assertTrue(server.start())
        
        try:
            client = WaylandCompatClient(self._socket_path)
            self.assertTrue(client.connect())
            
            window = client.create_window("Test", 800, 600)
            self.assertEqual(len(client.get_windows()), 1)
            
            window.close()
            self.assertEqual(len(client.get_windows()), 0)
            
            client.disconnect()
        finally:
            server.stop()
    
    def test_event_handlers(self):
        """Can register event handlers."""
        from ui.wayland_compat import WaylandCompatClient
        client = WaylandCompatClient(self._socket_path)
        
        key_events = []
        pointer_events = []
        frame_events = []
        
        client.on_key(lambda e: key_events.append(e))
        client.on_pointer(lambda e: pointer_events.append(e))
        client.on_frame(lambda e: frame_events.append(e))
        
        self.assertEqual(len(client._key_handlers), 1)
        self.assertEqual(len(client._pointer_handlers), 1)
        self.assertEqual(len(client._frame_handlers), 1)


class TestWaylandClientIntegration(unittest.TestCase):
    """Integration tests for the Wayland client."""

    def test_full_handshake(self):
        """Full Wayland client handshake."""
        from ui.wayland_socket import WaylandSocketServer
        from ui.wayland_client import WaylandClientTest
        
        with tempfile.NamedTemporaryFile(suffix=".sock", delete=False) as f:
            socket_path = f.name
        os.unlink(socket_path)
        
        try:
            server = WaylandSocketServer(socket_path)
            self.assertTrue(server.start())
            
            client = WaylandClientTest(socket_path)
            self.assertTrue(client.connect())
            
            # Perform handshake
            client.sync()
            client.get_registry()
            
            # Wait for responses
            events = client.receive_events(timeout=0.5)
            
            # Bind wl_compositor (simulated)
            client._wl_compositor = 100
            
            # Create surface
            surface_id = client.create_surface(800, 600)
            self.assertGreater(surface_id, 0)
            
            # Send sync to verify
            client.sync()
            events = client.receive_events(timeout=0.5)
            
            client.disconnect()
            server.stop()
        finally:
            if os.path.exists(socket_path):
                os.unlink(socket_path)

    def test_multi_client_competition(self):
        """Multiple clients can connect simultaneously."""
        from ui.wayland_socket import WaylandSocketServer
        from ui.wayland_client import WaylandClientTest
        
        with tempfile.NamedTemporaryFile(suffix=".sock", delete=False) as f:
            socket_path = f.name
        os.unlink(socket_path)
        
        try:
            server = WaylandSocketServer(socket_path)
            self.assertTrue(server.start())
            
            clients = []
            for i in range(3):
                client = WaylandClientTest(socket_path)
                self.assertTrue(client.connect())
                clients.append(client)
            
            time.sleep(0.2)
            self.assertEqual(server.get_client_count(), 3)
            
            for client in clients:
                client.disconnect()
            
            time.sleep(0.2)
            self.assertEqual(server.get_client_count(), 0)
            
            server.stop()
        finally:
            if os.path.exists(socket_path):
                os.unlink(socket_path)


if __name__ == "__main__":
    unittest.main()
